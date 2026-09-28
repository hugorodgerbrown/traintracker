"""The hosted HTTP app: OAuth sign-in (for claude.ai connectors), the static
bearer token, the health check and the Host check."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import secrets
from collections.abc import AsyncIterator
from urllib.parse import parse_qs, urlparse

import httpx
import psycopg
import pytest
from mcp.server.transport_security import TransportSecurityMiddleware
from starlette.requests import Request

from traintracker import oauth, server
from traintracker.config import Settings
from traintracker.http_app import build_app, transport_security
from traintracker.oauth import TraintrackerOAuthProvider
from traintracker.server import main

BASE = "https://tt.test"
CALLBACK = "https://claude.ai/api/mcp/auth_callback"
PASSPHRASE = "correct horse battery staple"


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("MCP_PUBLIC_URL", BASE)
    monkeypatch.setenv("MCP_PUBLIC_HOSTS", "tt.test")
    monkeypatch.setenv("MCP_OAUTH_PASSPHRASE", PASSPHRASE)
    monkeypatch.setenv("MCP_AUTH_TOKEN", "static-token")
    monkeypatch.setenv("MCP_AUTH_SCHEMA", os.environ["TIMETABLE_SCHEMA"] + "_auth")
    return Settings.from_env()


@pytest.fixture
async def client(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=build_app(server.mcp, settings))
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as c:
        yield c


def _provider(settings: Settings) -> TraintrackerOAuthProvider:
    provider = TraintrackerOAuthProvider(
        settings.database_url or "",
        settings.public_url,
        settings.oauth_passphrase,
        settings.mcp_auth_token,
        schema=settings.auth_schema,
    )
    provider.create_tables()
    return provider


async def _register(client: httpx.AsyncClient) -> str:
    r = await client.post(
        "/register",
        json={
            "client_name": "Claude",
            "redirect_uris": [CALLBACK],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
        },
    )
    assert r.status_code == 201, r.text
    return str(r.json()["client_id"])


async def _start_sign_in(client: httpx.AsyncClient, client_id: str) -> tuple[str, str]:
    """Run /authorize; return the sign-in request id and the PKCE verifier."""
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    r = await client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": CALLBACK,
            "code_challenge": challenge.decode().rstrip("="),
            "code_challenge_method": "S256",
            "state": "xyz",
            "resource": f"{BASE}/mcp",
        },
    )
    assert r.status_code == 302, r.text
    location = urlparse(r.headers["location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == f"{BASE}/sign-in"
    return parse_qs(location.query)["request"][0], verifier


async def _token(client: httpx.AsyncClient, **form: str) -> httpx.Response:
    return await client.post("/token", data=form)


async def test_discovery_and_unauthenticated_mcp(client: httpx.AsyncClient) -> None:
    meta = (await client.get("/.well-known/oauth-authorization-server")).json()
    assert meta["issuer"] == BASE
    assert meta["registration_endpoint"] == f"{BASE}/register"
    resource = (await client.get("/.well-known/oauth-protected-resource/mcp")).json()
    assert resource["resource"] == f"{BASE}/mcp"
    r = await client.post("/mcp", json={})
    assert r.status_code == 401
    assert "resource_metadata" in r.headers["www-authenticate"]
    assert (await client.get("/healthz")).text == "ok"


async def test_sign_in_issues_tokens_that_rotate_and_revoke(
    client: httpx.AsyncClient, settings: Settings
) -> None:
    provider = _provider(settings)
    client_id = await _register(client)
    sign_in, verifier = await _start_sign_in(client, client_id)

    page = await client.get("/sign-in", params={"request": sign_in})
    assert page.status_code == 200 and "Claude" in page.text
    wrong = await client.post("/sign-in", data={"request": sign_in, "passphrase": "nope"})
    assert wrong.status_code == 401 and "Wrong passphrase" in wrong.text

    ok = await client.post("/sign-in", data={"request": sign_in, "passphrase": PASSPHRASE})
    assert ok.status_code == 302
    back = urlparse(ok.headers["location"])
    assert f"{back.scheme}://{back.netloc}{back.path}" == CALLBACK
    query = parse_qs(back.query)
    assert query["state"] == ["xyz"]
    code = query["code"][0]

    exchange = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": CALLBACK,
        "client_id": client_id,
        "code_verifier": verifier,
        "resource": f"{BASE}/mcp",
    }
    tokens = (await _token(client, **exchange)).json()
    access, refresh = tokens["access_token"], tokens["refresh_token"]
    assert tokens["token_type"].lower() == "bearer"
    found = await provider.load_access_token(access)
    assert found is not None and found.resource == f"{BASE}/mcp"
    replay = await _token(client, **exchange)
    assert replay.status_code == 400 and replay.json()["error"] == "invalid_grant"

    rotated = await _token(
        client, grant_type="refresh_token", refresh_token=refresh, client_id=client_id
    )
    assert rotated.status_code == 200, rotated.text
    new_access = rotated.json()["access_token"]
    assert await provider.load_access_token(access) is None  # old pair retired
    reused = await _token(
        client, grant_type="refresh_token", refresh_token=refresh, client_id=client_id
    )
    assert reused.status_code == 400

    # Nothing secret is stored in the clear.
    with psycopg.connect(settings.database_url or "") as con:
        stored = con.execute(
            f"SELECT token_hash, data::text FROM {settings.auth_schema}.tokens"
        ).fetchall()
    dump = " ".join(" ".join(row) for row in stored)
    assert stored and refresh not in dump and new_access not in dump

    # The SDK's revocation model requires client_secret to be present, even for
    # public clients (token_endpoint_auth_method "none"), so send it empty.
    revoked = await client.post(
        "/revoke", data={"token": new_access, "client_id": client_id, "client_secret": ""}
    )
    assert revoked.status_code == 200
    assert await provider.load_access_token(new_access) is None


async def test_wrong_passphrases_end_the_sign_in(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(oauth, "MAX_ATTEMPTS", 1)
    client_id = await _register(client)
    sign_in, _ = await _start_sign_in(client, client_id)
    r = await client.post("/sign-in", data={"request": sign_in, "passphrase": "nope"})
    assert r.status_code == 403
    again = await client.post("/sign-in", data={"request": sign_in, "passphrase": PASSPHRASE})
    assert again.status_code == 400 and "expired" in again.text


async def test_parallel_wrong_passphrases_share_the_attempt_limit(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(oauth, "MAX_ATTEMPTS", 3)
    client_id = await _register(client)
    sign_in, _ = await _start_sign_in(client, client_id)
    responses = await asyncio.gather(
        *(
            client.post("/sign-in", data={"request": sign_in, "passphrase": f"guess {i}"})
            for i in range(10)
        )
    )
    checked = [r for r in responses if r.status_code != 400]  # passphrase was compared
    assert len(checked) == 3


async def test_codes_are_not_stored_in_the_clear(
    client: httpx.AsyncClient, settings: Settings
) -> None:
    client_id = await _register(client)
    sign_in, _ = await _start_sign_in(client, client_id)
    ok = await client.post("/sign-in", data={"request": sign_in, "passphrase": PASSPHRASE})
    code = parse_qs(urlparse(ok.headers["location"]).query)["code"][0]
    with psycopg.connect(settings.database_url or "") as con:
        stored = con.execute(f"SELECT data::text FROM {settings.auth_schema}.codes").fetchall()
    assert stored and all(code not in row[0] for row in stored)


async def test_failed_refresh_keeps_the_current_tokens(
    client: httpx.AsyncClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = _provider(settings)
    client_id = await _register(client)
    sign_in, verifier = await _start_sign_in(client, client_id)
    ok = await client.post("/sign-in", data={"request": sign_in, "passphrase": PASSPHRASE})
    code = parse_qs(urlparse(ok.headers["location"]).query)["code"][0]
    tokens = (
        await _token(
            client,
            grant_type="authorization_code",
            code=code,
            redirect_uri=CALLBACK,
            client_id=client_id,
            code_verifier=verifier,
        )
    ).json()
    registered = await provider.get_client(client_id)
    assert registered is not None
    refresh = await provider.load_refresh_token(registered, tokens["refresh_token"])
    assert refresh is not None

    def broken_issue(*_: object) -> tuple[list[tuple[str, tuple[()]]], None]:
        return [("INSERT INTO no_such_table VALUES (1)", ())], None

    monkeypatch.setattr(provider, "_issue", broken_issue)
    with pytest.raises(psycopg.Error):
        await provider.exchange_refresh_token(registered, refresh, [])
    # The rotation rolled back: the client's current pair still works.
    assert await provider.load_refresh_token(registered, tokens["refresh_token"]) is not None
    assert await provider.load_access_token(tokens["access_token"]) is not None


async def test_unknown_sign_in_link(client: httpx.AsyncClient) -> None:
    r = await client.get("/sign-in", params={"request": "made-up"})
    assert r.status_code == 400
    assert r.headers["x-frame-options"] == "DENY"


async def test_static_token_still_works(settings: Settings) -> None:
    provider = _provider(settings)
    token = await provider.load_access_token("static-token")
    assert token is not None and token.resource == f"{BASE}/mcp"
    assert await provider.load_access_token("static-token-nope") is None


def test_serve_http_needs_a_token_or_passphrase(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("MCP_OAUTH_PASSPHRASE", raising=False)
    with pytest.raises(SystemExit) as exit_info:
        main(["serve-http"])
    assert exit_info.value.code == 2


def _request(host: str) -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/mcp",
        "headers": [(b"host", host.encode()), (b"content-type", b"application/json")],
    }
    return Request(scope)


async def test_public_host_is_checked_apart_from_the_bind_address() -> None:
    settings = transport_security(["traintracker.onrender.com"])
    assert settings is not None
    middleware = TransportSecurityMiddleware(settings)
    assert await middleware.validate_request(_request("traintracker.onrender.com"), True) is None
    rejected = await middleware.validate_request(_request("evil.example"), True)
    assert rejected is not None and rejected.status_code == 421


def test_public_hosts_keep_the_render_hostname(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_PUBLIC_HOSTS", raising=False)
    monkeypatch.setenv("RENDER_EXTERNAL_HOSTNAME", "traintracker.onrender.com")
    assert Settings.from_env().public_hosts == ("traintracker.onrender.com",)
    # A custom domain is added to the Render name, not substituted for it.
    monkeypatch.setenv("MCP_PUBLIC_HOSTS", "a.example, b.example, traintracker.onrender.com")
    assert Settings.from_env().public_hosts == (
        "a.example",
        "b.example",
        "traintracker.onrender.com",
    )
    monkeypatch.delenv("RENDER_EXTERNAL_HOSTNAME")
    assert Settings.from_env().public_hosts == (
        "a.example",
        "b.example",
        "traintracker.onrender.com",
    )


def test_public_url_defaults_to_the_first_public_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_PUBLIC_URL", raising=False)
    monkeypatch.setenv("MCP_PUBLIC_HOSTS", "traintrackr.live")
    monkeypatch.setenv("RENDER_EXTERNAL_HOSTNAME", "traintracker.onrender.com")
    assert Settings.from_env().public_url == "https://traintrackr.live"


def test_no_public_hosts_keeps_sdk_default() -> None:
    assert transport_security([]) is None
