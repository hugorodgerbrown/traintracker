"""Tests for apps.core.ratelimit."""

from typing import Any

import pytest
from django.core.cache import cache
from django.test import RequestFactory

from apps.core.ratelimit import client_ip, over_limit


@pytest.fixture(autouse=True)
def _fresh_cache() -> None:
    """Counters live in the cache; start each test with none."""
    cache.clear()


def test_over_limit_counts_per_value() -> None:
    """The limit-th hit passes, the next doesn't, and other values count separately."""
    assert not any(over_limit("g", "a", limit=3, window_seconds=60) for _ in range(3))
    assert over_limit("g", "a", limit=3, window_seconds=60)
    assert not over_limit("g", "b", limit=3, window_seconds=60)
    assert not over_limit("other", "a", limit=3, window_seconds=60)


def test_client_ip_without_a_proxy(rf: RequestFactory, settings: Any) -> None:
    """With no trusted proxy, X-Forwarded-For is ignored: the client could have written it."""
    settings.RATE_LIMIT_PROXY_COUNT = 0
    request = rf.get("/", REMOTE_ADDR="10.0.0.1", headers={"X-Forwarded-For": "1.2.3.4"})
    assert client_ip(request) == "10.0.0.1"


def test_client_ip_behind_one_proxy(rf: RequestFactory, settings: Any) -> None:
    """Behind one proxy the client is the last entry, whatever came before it."""
    settings.RATE_LIMIT_PROXY_COUNT = 1
    request = rf.get("/", REMOTE_ADDR="10.0.0.1", headers={"X-Forwarded-For": "6.6.6.6, 1.2.3.4"})
    assert client_ip(request) == "1.2.3.4"
    assert client_ip(rf.get("/", REMOTE_ADDR="10.0.0.1")) == "10.0.0.1"
