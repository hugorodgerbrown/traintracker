"""Fixtures for the public site's tests: the same pages from production's server and Django."""

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
from django.core.cache import cache
from starlette.applications import Starlette

from apps.public import boards, views
from tests.traintracker.test_boards import BASE, FIELDS, FakeFetch
from traintracker import site
from traintracker.site import boards as tt


@pytest.fixture
def fetch() -> FakeFetch:
    """The board fetch both apps share: three trains at Liverpool Street."""
    return FakeFetch()


@pytest.fixture(autouse=True)
def django_site(settings: Any, monkeypatch: pytest.MonkeyPatch, fetch: FakeFetch) -> None:
    """Django's pages with production's values, and boards from ``fetch`` on a fresh loop."""
    cache.clear()
    boards.limiter.cache_clear()  # a full bucket for every address
    settings.SITE_URL = BASE
    settings.FIVEBAR_DOMAIN = "traintrackr.live"
    monkeypatch.setattr(views, "fields", lambda: FIELDS)
    shared = tt.SharedBoards(fetch)
    monkeypatch.setattr(boards, "shared", lambda: shared)

    def run(fn: Callable[..., Any], *args: Any) -> Any:
        return asyncio.run(fn(*args))

    monkeypatch.setattr(boards.runtime, "run", run)


@pytest.fixture
async def production(fetch: FakeFetch) -> AsyncIterator[httpx.AsyncClient]:
    """Production's site and boards, as `traintracker serve-http` serves them."""
    app = Starlette(routes=[*site.routes(FIELDS), *tt.routes(FIELDS, fetch, "x-forwarded-for")])
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
        yield client
