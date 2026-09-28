"""The bearer-token gate in front of the streamable-HTTP server."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from traintracker.http_app import BearerAuth
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
