"""Fixtures for the public site's tests: Django's pages, with boards from a fake fetch."""

import asyncio
from collections.abc import Callable
from typing import Any

import pytest
from django.core.cache import cache
from django.utils.csp import CSP

from apps.core import analytics
from apps.public import board as tt
from apps.public import boards, views
from tests.public.fakes import BASE, FIELDS, FakeChanges, FakeFetch
from tests.rail.conftest import _isolate_env  # noqa: F401  (autouse: a schema of its own)


@pytest.fixture
def fetch() -> FakeFetch:
    """The board fetch the views use: three trains at Liverpool Street."""
    return FakeFetch()


@pytest.fixture
def changes() -> FakeChanges:
    """The journeys with a change the boards use: Liverpool Street to Sudbury."""
    return FakeChanges()


@pytest.fixture(autouse=True)
def django_site(
    settings: Any, monkeypatch: pytest.MonkeyPatch, fetch: FakeFetch, changes: FakeChanges
) -> None:
    """Django's pages with production's values, and boards on a fresh loop.

    The boards' trains come from ``fetch``, and their journeys with a change
    from ``changes``.
    """
    cache.clear()
    boards.limiter.cache_clear()  # a full bucket for every address
    settings.SITE_URL = BASE
    settings.FIVEBAR_DOMAIN = "traintrackr.live"
    # As config/settings.py builds the policy with FIVEBAR_DOMAIN set.
    fivebar = [CSP.SELF, *analytics.csp_sources(settings.FIVEBAR_DOMAIN)]
    settings.SECURE_CSP = {**settings.SECURE_CSP, "script-src": fivebar, "connect-src": fivebar}
    monkeypatch.setattr(views, "fields", lambda: FIELDS)
    shared = tt.SharedBoards(fetch, changes)
    monkeypatch.setattr(boards, "shared", lambda: shared)

    def run(fn: Callable[..., Any], *args: Any) -> Any:
        """Run ``fn(*args)`` to its end on a loop of its own."""
        return asyncio.run(fn(*args))

    monkeypatch.setattr(boards.runtime, "run", run)
