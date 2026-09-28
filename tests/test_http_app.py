"""The bearer-token gate in front of the streamable-HTTP server."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from mcp.server.transport_security import TransportSecurityMiddleware
from starlette.requests import Request

from traintracker.config import Settings
from traintracker.http_app import BearerAuth, transport_security
from traintracker.server import main


async def _inner(scope: dict[str, Any], receive: Any, send: Any) -> None:
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"inner"})


def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=BearerAuth(_inner, "s3cret"))
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def test_health_check_needs_no_token() -> None:
    async with _client() as c:
        r = await c.get("/healthz")
    assert (r.status_code, r.text) == (200, "ok")


@pytest.mark.parametrize("header", [None, "Bearer wrong", "s3cret", "Basic s3cret"])
async def test_rejects_missing_or_wrong_token(header: str | None) -> None:
    headers = {"Authorization": header} if header else {}
    async with _client() as c:
        r = await c.post("/mcp", headers=headers)
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"


async def test_passes_the_right_token_through() -> None:
    async with _client() as c:
        r = await c.post("/mcp", headers={"Authorization": "Bearer s3cret"})
    assert (r.status_code, r.text) == (200, "inner")


def test_empty_token_is_refused() -> None:
    with pytest.raises(ValueError, match="empty"):
        BearerAuth(_inner, "")


def test_serve_http_requires_a_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
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


def test_no_public_hosts_keeps_sdk_default() -> None:
    assert transport_security([]) is None
