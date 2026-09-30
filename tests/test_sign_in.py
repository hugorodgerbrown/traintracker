"""Email sign-in: a one-time code proves the address, the account is a keyed
hash of it, and the sending limits hold. Resend is mocked; nothing is sent."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections.abc import AsyncIterator
from urllib.parse import parse_qs, urlparse

import httpx
import psycopg
import pytest
import respx

from traintracker import http_app, oauth, server
from traintracker.config import Settings
from traintracker.http_app import build_app, build_provider
from traintracker.mail import RESEND_URL
from traintracker.server import _sign_in_problem, main

from .test_http_app import BASE, CALLBACK, PASSPHRASE, _register, _start_sign_in, _token

ADDRESS = "Pat.Traveller@example.org"
SENDER = "Traintrackr <login@tt.test>"


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("MCP_PUBLIC_URL", BASE)
    monkeypatch.setenv("MCP_PUBLIC_HOSTS", "tt.test")
    monkeypatch.setenv("MCP_OAUTH_PASSPHRASE", PASSPHRASE)
    monkeypatch.setenv("MCP_AUTH_SCHEMA", os.environ["TIMETABLE_SCHEMA"] + "_auth")
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.setenv("MAIL_FROM", SENDER)
    monkeypatch.setenv("MCP_ACCOUNT_SECRET", "test-account-secret")
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("MAIL_BACKEND", raising=False)
    # Every request in a test comes from one address. The limit on an address
    # has its own test; the others are about the limits behind it.
    monkeypatch.setattr(http_app, "EMAIL_LIMIT", (100, 100.0))
    return Settings.from_env()


@pytest.fixture
async def client(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=build_app(server.mcp, settings))
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as c:
        yield c


@pytest.fixture
def resend() -> AsyncIterator[respx.Route]:
    with respx.mock(assert_all_called=False) as mock:
        yield mock.post(RESEND_URL).mock(return_value=httpx.Response(200, json={"id": "m1"}))


def _sent(route: respx.Route, index: int = -1) -> dict[str, str]:
    """One message handed to Resend, with the code picked out of its text."""
    request = route.calls[index].request
    assert request.headers["authorization"] == "Bearer re_test_key"
    body = json.loads(request.content)
    found = re.search(r"\b(\d{6})\b", body["text"])
    assert found
    assert len(body["to"]) == 1
    return {**body, "to": body["to"][0], "code": found.group(1)}


async def _begin(client: httpx.AsyncClient) -> tuple[str, str, str]:
    client_id = await _register(client)
    sign_in, verifier = await _start_sign_in(client, client_id)
    return client_id, sign_in, verifier


async def _ask(client: httpx.AsyncClient, sign_in: str, address: str = ADDRESS) -> httpx.Response:
    return await client.post("/sign-in/email", data={"request": sign_in, "email": address})


async def _enter(client: httpx.AsyncClient, sign_in: str, code: str) -> httpx.Response:
    return await client.post(
        "/sign-in/code", data={"request": sign_in, "email": ADDRESS, "code": code}
    )


def _wrong(code: str) -> str:
    return f"{(int(code) + 1) % 1_000_000:06d}"


TABLES = ("clients", "sign_ins", "codes", "tokens", "accounts", "mail_log")


def _dump(settings: Settings, tables: tuple[str, ...] = TABLES) -> str:
    """What the auth schema holds, as text."""
    with psycopg.connect(settings.database_url or "") as con:
        rows = [
            str(row)
            for table in tables
            for row in con.execute(f"SELECT * FROM {settings.auth_schema}.{table}").fetchall()
        ]
    return "\n".join(rows)


async def test_a_code_signs_in_and_the_tokens_carry_the_account(
    client: httpx.AsyncClient, settings: Settings, resend: respx.Route
) -> None:
    provider = build_provider(settings)
    client_id, sign_in, verifier = await _begin(client)

    page = await client.get("/sign-in", params={"request": sign_in})
    assert page.status_code == 200
    assert 'type="email"' in page.text and "Have a passphrase?" in page.text
    # The person can see who is asking and where the browser goes next.
    assert "Claude" in page.text and "claude.ai" in page.text

    asked = await _ask(client, sign_in)
    assert asked.status_code == 200 and "six-digit code" in asked.text
    mail = _sent(resend)
    assert mail["to"] == ADDRESS and mail["from"] == SENDER
    assert "tt.test" in mail["subject"] and mail["code"] in mail["subject"]
    during = _dump(settings)
    assert mail["code"] not in during  # the pending code is stored as a hash

    wrong = await _enter(client, sign_in, _wrong(mail["code"]))
    assert wrong.status_code == 401 and "Wrong code" in wrong.text
    # Codes are often pasted with a space in the middle.
    ok = await _enter(client, sign_in, f"{mail['code'][:3]} {mail['code'][3:]}")
    assert ok.status_code == 302
    back = urlparse(ok.headers["location"])
    assert f"{back.scheme}://{back.netloc}{back.path}" == CALLBACK
    code = parse_qs(back.query)["code"][0]

    tokens = (
        await _token(
            client,
            grant_type="authorization_code",
            code=code,
            redirect_uri=CALLBACK,
            client_id=client_id,
            code_verifier=verifier,
            resource=f"{BASE}/mcp",
        )
    ).json()
    account = provider.account_id(ADDRESS)
    assert account == provider.account_id(" pat.traveller@EXAMPLE.org ")
    # A +tag reaches the same mailbox, so it is the same account.
    assert account == provider.account_id("pat.traveller+trains@example.org")
    assert account != provider.account_id("pat@example.org")
    access = await provider.load_access_token(tokens["access_token"])
    assert access is not None and access.subject == account

    rotated = await _token(
        client,
        grant_type="refresh_token",
        refresh_token=tokens["refresh_token"],
        client_id=client_id,
    )
    assert rotated.status_code == 200, rotated.text
    again = await provider.load_access_token(rotated.json()["access_token"])
    assert again is not None and again.subject == account

    # The account is known only by its keyed hash: the address is nowhere.
    stored = _dump(settings)
    assert account in stored
    assert "example.org" not in stored.lower() and "traveller" not in stored.lower()


async def test_wrong_codes_end_the_sign_in(
    client: httpx.AsyncClient, resend: respx.Route, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(oauth, "MAX_ATTEMPTS", 2)
    _, sign_in, _ = await _begin(client)
    await _ask(client, sign_in)
    code = _sent(resend)["code"]
    assert (await _enter(client, sign_in, _wrong(code))).status_code == 401
    last = await _enter(client, sign_in, _wrong(code))
    assert last.status_code == 403 and "Too many wrong codes" in last.text
    assert (await _enter(client, sign_in, code)).status_code == 400


async def test_an_expired_code_is_refused(
    client: httpx.AsyncClient, resend: respx.Route, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(oauth, "LOGIN_CODE_TTL", -1)
    _, sign_in, _ = await _begin(client)
    await _ask(client, sign_in)
    late = await _enter(client, sign_in, _sent(resend)["code"])
    assert late.status_code == 401 and "expired" in late.text


async def test_a_code_only_works_for_its_own_sign_in(
    client: httpx.AsyncClient, resend: respx.Route
) -> None:
    _, first, _ = await _begin(client)
    _, second, _ = await _begin(client)
    await _ask(client, first)
    code = _sent(resend)["code"]
    # No code was asked for on the second sign-in, so there is nothing to match.
    assert (await _enter(client, second, code)).status_code == 400
    await _ask(client, second, "someone.else@example.org")
    assert (await _enter(client, second, code)).status_code == 401


async def test_a_new_code_replaces_the_old_one(
    client: httpx.AsyncClient, resend: respx.Route
) -> None:
    _, sign_in, _ = await _begin(client)
    await _ask(client, sign_in)
    await _ask(client, sign_in)
    old, new = _sent(resend, 0)["code"], _sent(resend, 1)["code"]
    if old != new:  # one time in a million they match
        assert (await _enter(client, sign_in, old)).status_code == 401
    assert (await _enter(client, sign_in, new)).status_code == 302


async def test_a_sign_in_can_ask_for_a_few_codes_only(
    client: httpx.AsyncClient, resend: respx.Route, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(oauth, "MAX_SENDS", 2)
    _, sign_in, _ = await _begin(client)
    assert (await _ask(client, sign_in)).status_code == 200
    assert (await _ask(client, sign_in)).status_code == 200
    third = await _ask(client, sign_in)
    assert third.status_code == 429 and "Too many codes requested" in third.text
    assert resend.call_count == 2
    assert (await _enter(client, sign_in, _sent(resend)["code"])).status_code == 400


async def test_an_address_is_sent_a_few_codes_an_hour_only(
    client: httpx.AsyncClient, resend: respx.Route, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(oauth, "MAX_CODES_PER_ADDRESS", 1)
    _, first, _ = await _begin(client)
    _, second, _ = await _begin(client)
    assert (await _ask(client, first)).status_code == 200
    # A fresh sign-in doesn't get round the limit on the address.
    refused = await _ask(client, second)
    assert refused.status_code == 429 and "Too many codes have been sent" in refused.text
    assert (await _ask(client, second, "someone.else@example.org")).status_code == 200
    assert resend.call_count == 2


async def test_codes_asked_for_at_once_share_the_limit(
    client: httpx.AsyncClient, resend: respx.Route, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(oauth, "MAX_CODES_PER_ADDRESS", 2)
    sign_ins = [(await _begin(client))[1] for _ in range(8)]
    answers = await asyncio.gather(*(_ask(client, sign_in) for sign_in in sign_ins))
    # Each asks before any other has been logged; only the lock keeps the count true.
    assert sorted(r.status_code for r in answers) == [200] * 2 + [429] * 6
    assert resend.call_count == 2


async def test_a_code_asked_for_late_keeps_the_sign_in_open(
    client: httpx.AsyncClient, settings: Settings, resend: respx.Route
) -> None:
    _, sign_in, _ = await _begin(client)
    table = f"{settings.auth_schema}.sign_ins"
    with psycopg.connect(settings.database_url or "", autocommit=True) as con:
        # The sign-in has seconds left of its own ten minutes.
        con.execute(f"UPDATE {table} SET expires_at = %s WHERE id = %s", (time.time() + 5, sign_in))
        assert (await _ask(client, sign_in)).status_code == 200
        until, code_until = con.execute(
            f"SELECT expires_at, login_code_expires_at FROM {table} WHERE id = %s", (sign_in,)
        ).fetchone() or (0, 0)
    assert until == code_until > time.time() + oauth.LOGIN_CODE_TTL - 5


async def test_all_mail_is_capped_by_the_hour(
    settings: Settings,
    resend: respx.Route,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("MAIL_MAX_PER_HOUR", "1")
    transport = httpx.ASGITransport(app=build_app(server.mcp, Settings.from_env()))
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
        _, sign_in, _ = await _begin(client)
        assert (await _ask(client, sign_in)).status_code == 200
        assert (await _ask(client, sign_in, "someone.else@example.org")).status_code == 429
        assert (await _ask(client, sign_in, "a.third@example.org")).status_code == 429
    assert resend.call_count == 1
    # The owner is told, once, that nobody can be sent a code.
    assert caplog.text.count("Sign-in mail is at its limit of 1 an hour") == 1


async def test_an_address_limit_on_codes_is_not_reported_as_the_server_limit(
    client: httpx.AsyncClient,
    resend: respx.Route,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(oauth, "MAX_CODES_PER_ADDRESS", 1)
    _, first, _ = await _begin(client)
    _, second, _ = await _begin(client)
    await _ask(client, first)
    assert (await _ask(client, second)).status_code == 429
    assert "Sign-in mail is at its limit" not in caplog.text


async def test_one_client_address_can_ask_for_a_few_codes_only(
    settings: Settings, resend: respx.Route, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Whatever addresses are typed in, and however many sign-ins are started.
    monkeypatch.setattr(http_app, "EMAIL_LIMIT", (2, 10 / 60))
    transport = httpx.ASGITransport(app=build_app(server.mcp, settings))
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
        answers = []
        for n in range(3):
            _, sign_in, _ = await _begin(client)
            answers.append(await _ask(client, sign_in, f"person{n}@example.org"))
    assert [r.status_code for r in answers] == [200, 200, 429]
    assert answers[2].text.startswith("Too many requests")
    assert resend.call_count == 2


async def test_an_address_must_look_like_one(
    client: httpx.AsyncClient, resend: respx.Route
) -> None:
    _, sign_in, _ = await _begin(client)
    for address in ("", "pat", "pat@example", "pat @example.org", "a" * 250 + "@example.org"):
        refused = await _ask(client, sign_in, address)
        assert refused.status_code == 400, address
        assert "Enter a full email address" in refused.text
    assert resend.call_count == 0


async def test_a_mail_failure_is_reported_and_can_be_retried(
    client: httpx.AsyncClient, resend: respx.Route, caplog: pytest.LogCaptureFixture
) -> None:
    resend.mock(return_value=httpx.Response(500, json={"message": f"cannot send to {ADDRESS}"}))
    _, sign_in, _ = await _begin(client)
    failed = await _ask(client, sign_in)
    assert failed.status_code == 502 and "could not be sent" in failed.text
    assert ADDRESS not in caplog.text  # the address stays out of the logs
    resend.mock(return_value=httpx.Response(200, json={"id": "m2"}))
    assert (await _ask(client, sign_in)).status_code == 200
    assert (await _enter(client, sign_in, _sent(resend)["code"])).status_code == 302


async def _sign_in(client: httpx.AsyncClient, resend: respx.Route) -> str:
    """Sign in by email; returns the access token."""
    client_id, sign_in, verifier = await _begin(client)
    await _ask(client, sign_in)
    ok = await _enter(client, sign_in, _sent(resend)["code"])
    assert ok.status_code == 302, ok.text
    tokens = await _token(
        client,
        grant_type="authorization_code",
        code=parse_qs(urlparse(ok.headers["location"]).query)["code"][0],
        redirect_uri=CALLBACK,
        client_id=client_id,
        code_verifier=verifier,
    )
    return str(tokens.json()["access_token"])


async def test_a_blocked_account_is_shut_out(
    client: httpx.AsyncClient, settings: Settings, resend: respx.Route
) -> None:
    provider = build_provider(settings)
    access = await _sign_in(client, resend)
    assert await provider.load_access_token(access) is not None
    provider.block(ADDRESS)
    assert await provider.load_access_token(access) is None
    _, sign_in, _ = await _begin(client)
    sent = resend.call_count
    refused = await _ask(client, sign_in)
    assert refused.status_code == 403 and "sign in to this server" in refused.text
    assert resend.call_count == sent


async def test_forget_deletes_the_account_and_its_tokens(
    client: httpx.AsyncClient,
    settings: Settings,
    resend: respx.Route,
    capsys: pytest.CaptureFixture[str],
) -> None:
    provider = build_provider(settings)
    access = await _sign_in(client, resend)
    main(["forget", ADDRESS])
    assert "Forgotten" in capsys.readouterr().err
    assert await provider.load_access_token(access) is None
    assert provider.account_id(ADDRESS) not in _dump(settings)  # mail log included
    main(["forget", ADDRESS])
    assert "No account" in capsys.readouterr().err


async def test_unused_accounts_are_deleted(
    client: httpx.AsyncClient, settings: Settings, resend: respx.Route
) -> None:
    provider = build_provider(settings)
    access = await _sign_in(client, resend)
    with psycopg.connect(settings.database_url or "", autocommit=True) as con:
        con.execute(
            f"UPDATE {settings.auth_schema}.accounts SET last_seen_at = now() - interval '181 days'"
        )
    # The next person to sign in clears out accounts that have gone unused.
    assert await provider._record_sign_in(provider.account_id("someone.else@example.org"))
    assert await provider.load_access_token(access) is None
    # The mail log is left to its own 24-hour purge.
    assert provider.account_id(ADDRESS) not in _dump(settings, ("accounts", "tokens"))


async def test_the_passphrase_still_signs_in(client: httpx.AsyncClient, settings: Settings) -> None:
    provider = build_provider(settings)
    client_id, sign_in, verifier = await _begin(client)
    wrong = await client.post("/sign-in", data={"request": sign_in, "passphrase": "nope"})
    # The passphrase field is folded away until it is the thing that went wrong.
    assert wrong.status_code == 401 and "<details open>" in wrong.text
    ok = await client.post("/sign-in", data={"request": sign_in, "passphrase": PASSPHRASE})
    assert ok.status_code == 302
    tokens = await _token(
        client,
        grant_type="authorization_code",
        code=parse_qs(urlparse(ok.headers["location"]).query)["code"][0],
        redirect_uri=CALLBACK,
        client_id=client_id,
        code_verifier=verifier,
    )
    access = await provider.load_access_token(tokens.json()["access_token"])
    assert access is not None and access.subject == oauth.PASSPHRASE_SUBJECT


async def test_email_sign_in_is_off_without_a_mailer(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("RESEND_API_KEY", "MAIL_FROM", "MCP_ACCOUNT_SECRET"):
        monkeypatch.delenv(name)
    transport = httpx.ASGITransport(app=build_app(server.mcp, Settings.from_env()))
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
        _, sign_in, _ = await _begin(client)
        page = await client.get("/sign-in", params={"request": sign_in})
        # As before this change: the passphrase, and nothing else.
        assert 'type="email"' not in page.text and "<details" not in page.text
        assert 'type="password"' in page.text
        off = await _ask(client, sign_in)
        assert "turned off" in off.text


def test_serve_http_checks_the_sign_in_settings(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _sign_in_problem(settings) is None
    monkeypatch.delenv("MCP_OAUTH_PASSPHRASE")
    assert _sign_in_problem(Settings.from_env()) is None  # email codes alone will do

    monkeypatch.delenv("MAIL_FROM")
    partial = Settings.from_env()
    assert not partial.email_sign_in
    assert _sign_in_problem(partial) == ("Email sign-in is partly configured: also set MAIL_FROM.")
    with pytest.raises(SystemExit) as exit_info:
        main(["serve-http"])
    assert exit_info.value.code == 2

    # The console mailer needs no provider, only the key for account IDs.
    monkeypatch.delenv("RESEND_API_KEY")
    monkeypatch.setenv("MAIL_BACKEND", "console")
    assert _sign_in_problem(Settings.from_env()) is None
    monkeypatch.delenv("MCP_ACCOUNT_SECRET")
    assert "MCP_ACCOUNT_SECRET" in (_sign_in_problem(Settings.from_env()) or "")

    monkeypatch.delenv("MAIL_BACKEND")
    assert "needs a way to sign in" in (_sign_in_problem(Settings.from_env()) or "")


async def test_the_console_mailer_logs_the_code(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("MAIL_BACKEND", "console")
    transport = httpx.ASGITransport(app=build_app(server.mcp, Settings.from_env()))
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
        _, sign_in, _ = await _begin(client)
        with caplog.at_level("WARNING"):
            assert (await _ask(client, sign_in)).status_code == 200
        found = re.search(r": (\d{6}) \(MAIL_BACKEND=console\)", caplog.text)
        assert found
        assert (await _enter(client, sign_in, found.group(1))).status_code == 302
