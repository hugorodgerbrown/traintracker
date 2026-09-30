"""The per-account rate limiter: the bucket itself, who a request is counted
against, and a limited tool call as a client sees it over HTTP."""

from __future__ import annotations

import os
from typing import Any

import httpx
import httpx2
import pytest
import respx
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from starlette.requests import Request

from traintracker import ratelimit, server
from traintracker.config import DARWIN_DEPARTURES_URL, Settings
from traintracker.http_app import build_app
from traintracker.ratelimit import (
    RateLimiter,
    RateLimitMiddleware,
    account_key,
    charge_upstream,
    limited,
)

from .test_server import _darwin_board, _darwin_svc

BASE = "https://tt.test"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_bucket_empties_reports_the_wait_and_refills() -> None:
    clock = Clock()
    limiter = RateLimiter(per_minute=30, burst=2, clock=clock)  # one call per 2 seconds
    assert limiter.take("a") == 0
    assert limiter.take("a") == 0
    assert limiter.take("a") == 2
    clock.now += 1
    assert limiter.take("a") == 1
    clock.now += 1
    assert limiter.take("a") == 0
    assert limiter.take("a") == 2
    # A long rest fills the bucket, and no further.
    clock.now += 3600
    assert [limiter.take("a") for _ in range(3)] == [0, 0, 2]


def test_accounts_have_separate_buckets() -> None:
    limiter = RateLimiter(per_minute=1, burst=1, clock=Clock())
    assert limiter.take("a") == 0
    assert limiter.take("a") == 60
    assert limiter.take("b") == 0


def test_idle_buckets_are_forgotten() -> None:
    clock = Clock()
    limiter = RateLimiter(per_minute=60, burst=1, clock=clock, max_keys=2)
    limiter.take("a")
    clock.now += 0.5
    limiter.take("b")
    clock.now += 0.6  # "a" has refilled, "b" has not
    limiter.take("c")
    assert set(limiter._buckets) == {"b", "c"}
    limiter.take("d")  # nothing has refilled: the longest idle goes
    assert set(limiter._buckets) == {"c", "d"}


def test_a_charge_puts_the_bucket_in_debt_but_no_deeper_than_a_full_one() -> None:
    clock = Clock()
    limiter = RateLimiter(per_minute=60, burst=2, clock=clock)  # one a second
    assert limiter.take("a") == 0
    for _ in range(10):
        limiter.charge("a")
    assert limiter.take("a") == 3  # from two in debt back up to one
    clock.now += 3
    assert limiter.take("a") == 0


def test_a_call_covers_its_first_upstream_request_and_is_charged_for_the_rest() -> None:
    limiter = RateLimiter(per_minute=60, burst=5, clock=Clock())
    charge_upstream()  # no call in progress (stdio): nothing to charge
    assert limiter.take("a") == 0
    current = ratelimit._call.set(ratelimit._Call(limiter, "a"))
    try:
        charge_upstream()
        assert limiter._buckets["a"].tokens == 4
        charge_upstream()
        charge_upstream()
        assert limiter._buckets["a"].tokens == 2
    finally:
        ratelimit._call.reset(current)


def test_the_message_reads_as_a_sentence() -> None:
    assert limited(1) == "Too many requests. Try again in 1 second."
    assert limited(12) == "Too many requests. Try again in 12 seconds."


def _request(token: AccessToken | None) -> Request:
    scope: dict[str, Any] = {"type": "http", "method": "POST", "path": "/mcp", "headers": []}
    if token:
        scope["user"] = AuthenticatedUser(token)
    return Request(scope)


def test_calls_are_counted_against_the_account() -> None:
    signed_in = AccessToken(token="t", client_id="client-1", scopes=[], subject="account-1")
    legacy = AccessToken(token="t", client_id="client-1", scopes=[])
    assert account_key(_request(signed_in)) == "account-1"
    # Tokens issued before accounts existed, and the static token, have no subject.
    assert account_key(_request(legacy)) == "client-1"
    assert account_key(_request(None)) is None
    assert account_key(None) is None  # stdio


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("MCP_PUBLIC_URL", BASE)
    monkeypatch.setenv("MCP_PUBLIC_HOSTS", "tt.test")
    monkeypatch.setenv("MCP_AUTH_TOKEN", "static-token")
    monkeypatch.setenv("MCP_AUTH_SCHEMA", os.environ["TIMETABLE_SCHEMA"] + "_auth")
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "1")
    monkeypatch.setenv("RATE_LIMIT_BURST", "2")
    return Settings.from_env()


async def test_a_limited_call_is_a_tool_error_the_model_can_read(settings: Settings) -> None:
    app = build_app(server.mcp, settings)
    http = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app),
        base_url=BASE,
        headers={"Authorization": "Bearer static-token"},
    )
    async with (
        app.router.lifespan_context(app),
        http,
        Client(streamable_http_client(f"{BASE}/mcp", http_client=http)) as client,
    ):
        assert len((await client.list_tools()).tools) == 10  # listing is not limited
        results = [await client.call_tool("find_station", {"query": "sudbury"}) for _ in range(3)]
    assert [r.is_error for r in results] == [False, False, True]
    text = " ".join(getattr(c, "text", "") for c in results[2].content)
    assert text.startswith("Too many requests. Try again in ") and text.endswith(" seconds.")


@respx.mock
async def test_a_call_that_pages_through_a_board_pays_for_each_request(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DARWIN_API_KEY", "darwin-key")
    # Ten services is a full page, so the board is read page by page: three
    # requests to Darwin for the one call, and a bucket of two is left in debt.
    first = [_darwin_svc(f"13:{m:02d}", "2") for m in range(1, 11)]
    second = [_darwin_svc(f"13:{m:02d}", "2") for m in range(10, 20)]
    route = respx.get(f"{DARWIN_DEPARTURES_URL}/GetDepBoardWithDetails/LST").mock(
        side_effect=lambda request: httpx.Response(
            200, json=_darwin_board(first if request.url.params["timeOffset"] == "0" else second)
        )
    )
    app = build_app(server.mcp, settings)
    http = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app),
        base_url=BASE,
        headers={"Authorization": "Bearer static-token"},
    )
    async with (
        app.router.lifespan_context(app),
        http,
        Client(streamable_http_client(f"{BASE}/mcp", http_client=http)) as client,
    ):
        board = await client.call_tool("platform_departures", {"station": "LST", "platform": "9"})
        after = await client.call_tool("find_station", {"query": "sudbury"})
    assert not board.is_error and route.call_count == 3
    assert after.is_error


def test_the_limiter_is_replaced_not_stacked(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    build_app(server.mcp, settings)
    build_app(server.mcp, settings)
    assert sum(isinstance(m, RateLimitMiddleware) for m in server.mcp.middleware) == 1
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "0")
    build_app(server.mcp, Settings.from_env())
    assert not any(isinstance(m, RateLimitMiddleware) for m in server.mcp.middleware)
