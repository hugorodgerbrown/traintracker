"""The hosted HTTP app: OAuth sign-in (for claude.ai connectors), the static
bearer token, the health check and the Host check."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import secrets
from collections.abc import AsyncIterator
from urllib.parse import parse_qs, urlparse

import httpx
import psycopg
import pytest
from mcp.server.transport_security import TransportSecurityMiddleware
from starlette.requests import Request

from traintracker import http_app, oauth, server
from traintracker.config import Settings
from traintracker.http_app import (
    MAX_MCP_BODY,
    MAX_OPEN_BODY,
    OpenEndpointLimits,
    build_app,
    transport_security,
)
from traintracker.oauth import TraintrackerOAuthProvider
from traintracker.server import main

BASE = "https://tt.test"
CALLBACK = "https://claude.ai/api/mcp/auth_callback"
PASSPHRASE = "correct horse battery staple"
REGISTRATION = {
    "client_name": "Claude",
    "redirect_uris": [CALLBACK],
    "token_endpoint_auth_method": "none",
    "grant_types": ["authorization_code", "refresh_token"],
    "response_types": ["code"],
}


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
    r = await client.post("/register", json=REGISTRATION)
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


async def test_an_oversized_mcp_request_is_refused(client: httpx.AsyncClient) -> None:
    r = await client.post(
        "/mcp",
        headers={"Authorization": "Bearer static-token"},
        json={"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {"pad": "x" * MAX_MCP_BODY}},
    )
    assert r.status_code == 413


async def test_a_registration_must_be_small(client: httpx.AsyncClient, settings: Settings) -> None:
    # Anyone can register, so a registration can't be used to store much.
    wordy = await client.post("/register", json={**REGISTRATION, "client_name": "C" * 5000})
    assert wordy.status_code == 400 and "too large" in wordy.json()["error_description"]
    huge = await client.post("/register", json={**REGISTRATION, "client_name": "C" * MAX_OPEN_BODY})
    assert huge.status_code == 413
    with psycopg.connect(settings.database_url or "") as con:
        assert con.execute(f"SELECT count(*) FROM {settings.auth_schema}.clients").fetchone() == (
            0,
        )


async def test_registration_stops_at_the_client_limit(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(oauth, "MAX_CLIENTS", 2)
    await _register(client)
    await _register(client)
    full = await client.post("/register", json=REGISTRATION)
    assert full.status_code == 400 and "Try again later" in full.json()["error_description"]


async def test_clients_nobody_uses_are_deleted(
    client: httpx.AsyncClient, settings: Settings
) -> None:
    provider = _provider(settings)
    abandoned, pending, in_use = [await _register(client) for _ in range(3)]
    await _start_sign_in(client, pending)
    sign_in, verifier = await _start_sign_in(client, in_use)
    ok = await client.post("/sign-in", data={"request": sign_in, "passphrase": PASSPHRASE})
    code = parse_qs(urlparse(ok.headers["location"]).query)["code"][0]
    tokens = await _token(
        client,
        grant_type="authorization_code",
        code=code,
        redirect_uri=CALLBACK,
        client_id=in_use,
        code_verifier=verifier,
    )
    assert tokens.status_code == 200
    with psycopg.connect(settings.database_url or "", autocommit=True) as con:
        con.execute(
            f"UPDATE {settings.auth_schema}.clients SET created_at = now() - interval '8 days'"
        )
    await _register(client)  # the next registration clears them out
    assert await provider.get_client(abandoned) is None
    assert await provider.get_client(pending) is not None
    assert await provider.get_client(in_use) is not None


async def test_an_oversized_authorization_request_is_refused(client: httpx.AsyncClient) -> None:
    client_id = await _register(client)
    r = await client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": CALLBACK,
            "code_challenge": "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
            "state": "s" * 9000,
        },
    )
    assert r.status_code == 302 and "error=invalid_request" in r.headers["location"]


async def test_a_client_can_only_return_to_a_known_app_or_a_loopback_address(
    client: httpx.AsyncClient, settings: Settings
) -> None:
    async def register(*uris: str) -> httpx.Response:
        return await client.post("/register", json={**REGISTRATION, "redirect_uris": list(uris)})

    for uri in (
        "https://evil.example/cb",
        "http://claude.ai/api/mcp/auth_callback",  # not https
        "https://claude.ai.evil.example/cb",
        "javascript:alert(1)",
    ):
        refused = await register(CALLBACK, uri)
        assert refused.status_code == 400, uri
        assert refused.json()["error"] == "invalid_redirect_uri"
    for uri in (
        "https://chatgpt.com/connector_platform_oauth_redirect",
        "http://127.0.0.1:6274/oauth/callback",
        "http://localhost:8766/callback",
        "http://[::1]:9000/callback",
    ):
        assert (await register(uri)).status_code == 201, uri

    # A client stored before the rule is not redirected to: not on an error
    # either, which is how /authorize could be made to send a browser anywhere.
    stored = {**REGISTRATION, "client_id": "old", "redirect_uris": ["https://evil.example/cb"]}
    with psycopg.connect(settings.database_url or "", autocommit=True) as con:
        con.execute(
            f"INSERT INTO {settings.auth_schema}.clients (client_id, info) VALUES (%s, %s)",
            ("old", json.dumps(stored)),
        )
    r = await client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": "old",
            "redirect_uri": "https://evil.example/cb",
            "code_challenge": "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
            "scope": "no-such-scope",
        },
    )
    assert r.status_code == 400 and "location" not in r.headers


async def test_the_apps_a_client_can_return_to_are_configurable(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MCP_REDIRECT_HOSTS", "App.Example, other.example")
    assert Settings.from_env().redirect_hosts == ("app.example", "other.example")
    transport = httpx.ASGITransport(app=build_app(server.mcp, Settings.from_env()))
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as c:
        ok = await c.post(
            "/register", json={**REGISTRATION, "redirect_uris": ["https://app.example/cb"]}
        )
        assert ok.status_code == 201
        assert (await c.post("/register", json=REGISTRATION)).status_code == 400  # claude.ai
    monkeypatch.setenv("MCP_REDIRECT_HOSTS", "*")
    transport = httpx.ASGITransport(app=build_app(server.mcp, Settings.from_env()))
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as c:
        anywhere = await c.post(
            "/register", json={**REGISTRATION, "redirect_uris": ["cursor://anysphere/cb"]}
        )
        assert anywhere.status_code == 201


async def test_each_address_may_register_at_a_limited_rate(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(http_app, "REGISTER_LIMIT", (2, 1.0))
    monkeypatch.setenv("CLIENT_IP_HEADER", "CF-Connecting-IP")
    transport = httpx.ASGITransport(app=build_app(server.mcp, Settings.from_env()))
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as c:

        async def register(address: str) -> httpx.Response:
            return await c.post(
                "/register", json=REGISTRATION, headers={"CF-Connecting-IP": address}
            )

        assert [(await register("203.0.113.7")).status_code for _ in range(3)] == [201, 201, 429]
        refused = await register("203.0.113.7")
        assert int(refused.headers["retry-after"]) > 0
        # Another address has its own count, and other endpoints are not held up.
        assert (await register("203.0.113.8")).status_code == 201
        assert (await c.get("/healthz")).status_code == 200


def test_the_caller_is_the_connection_unless_a_proxy_header_is_trusted() -> None:
    scope = {
        "type": "http",
        "client": ("10.0.0.1", 4000),
        "headers": [(b"cf-connecting-ip", b"203.0.113.7")],
    }

    async def app(*_: object) -> None: ...

    assert OpenEndpointLimits(app, {}).address(scope) == "10.0.0.1"
    assert OpenEndpointLimits(app, {}, "CF-Connecting-IP").address(scope) == "203.0.113.7"
    assert OpenEndpointLimits(app, {}, "X-Real-IP").address(scope) == "10.0.0.1"


@pytest.mark.usefixtures("settings")
async def test_openai_domain_challenge(monkeypatch: pytest.MonkeyPatch) -> None:
    # OpenAI's directory proves the domain by fetching this path: the token
    # alone, as plain text, with nothing around it.
    monkeypatch.setenv("OPENAI_APPS_CHALLENGE", " token-from-the-portal ")
    transport = httpx.ASGITransport(app=build_app(server.mcp, Settings.from_env()))
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as c:
        r = await c.get("/.well-known/openai-apps-challenge")
    assert r.status_code == 200
    assert r.text == "token-from-the-portal"
    assert r.headers["content-type"].startswith("text/plain")


async def test_no_openai_challenge_unless_configured(client: httpx.AsyncClient) -> None:
    assert (await client.get("/.well-known/openai-apps-challenge")).status_code == 404


async def test_metadata_advertises_refresh_tokens_and_public_clients(
    client: httpx.AsyncClient,
) -> None:
    # ChatGPT keeps a connection past the first hour only if the server lists
    # offline_access; Claude asks for it when it is listed. Registration already
    # accepts public clients, so the metadata says so too.
    meta = (await client.get("/.well-known/oauth-authorization-server")).json()
    assert meta["scopes_supported"] == ["offline_access"]
    assert "none" in meta["token_endpoint_auth_methods_supported"]
    assert "client_secret_post" in meta["token_endpoint_auth_methods_supported"]
    assert meta["grant_types_supported"] == ["authorization_code", "refresh_token"]
    assert meta["registration_endpoint"] == f"{BASE}/register"


async def _authorize(
    client: httpx.AsyncClient, client_id: str, scope: str | None
) -> httpx.Response:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": CALLBACK,
        "code_challenge": "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
        "code_challenge_method": "S256",
        "state": "xyz",
        "resource": f"{BASE}/mcp",
    }
    if scope is not None:
        params["scope"] = scope
    return await client.get("/authorize", params=params)


async def test_offline_access_is_allowed_for_every_client(client: httpx.AsyncClient) -> None:
    # Clients registered before offline_access was advertised, and clients that
    # register without a scope, ask for it once they read the metadata.
    client_id = await _register(client)
    for scope in (None, "offline_access"):
        r = await _authorize(client, client_id, scope)
        assert r.status_code == 302, r.text
        assert urlparse(r.headers["location"]).path == "/sign-in", scope
    # Other scopes are still refused.
    refused = await _authorize(client, client_id, "admin")
    assert "error=invalid_scope" in refused.headers["location"]


async def test_a_sign_in_asking_for_offline_access_can_refresh(
    client: httpx.AsyncClient, settings: Settings
) -> None:
    client_id = await _register(client)
    started = await _authorize(client, client_id, "offline_access")
    sign_in = parse_qs(urlparse(started.headers["location"]).query)["request"][0]
    ok = await client.post("/sign-in", data={"request": sign_in, "passphrase": PASSPHRASE})
    code = parse_qs(urlparse(ok.headers["location"]).query)["code"][0]
    tokens = await _token(
        client,
        grant_type="authorization_code",
        code=code,
        redirect_uri=CALLBACK,
        client_id=client_id,
        # RFC 7636 appendix B: the verifier for the challenge _authorize sends.
        code_verifier="dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk",
    )
    assert tokens.status_code == 200, tokens.text
    assert tokens.json()["scope"] == "offline_access"
    rotated = await _token(
        client,
        grant_type="refresh_token",
        refresh_token=tokens.json()["refresh_token"],
        client_id=client_id,
    )
    assert rotated.status_code == 200, rotated.text
    access = await _provider(settings).load_access_token(rotated.json()["access_token"])
    assert access is not None and access.scopes == ["offline_access"]


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
    # The browser must be allowed to follow the redirect to the client's callback:
    # a form-action CSP on the sign-in page blocks it.
    assert "form-action" not in page.headers["content-security-policy"]
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
