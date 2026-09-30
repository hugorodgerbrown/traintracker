"""Small shared HTTP helpers: a TTL cache and response error mapping."""

from __future__ import annotations

import logging
import time
from collections.abc import Hashable
from typing import Any

import httpx

from traintracker.errors import RateLimited, UpstreamError

log = logging.getLogger(__name__)


class TTLCache:
    """Tiny in-memory cache. Live data goes stale fast, so keep TTLs short."""

    def __init__(self, max_items: int = 256) -> None:
        self._data: dict[Hashable, tuple[float, Any]] = {}
        self._max = max_items

    def get(self, key: Hashable) -> Any | None:
        hit = self._data.get(key)
        if hit is None:
            return None
        expires, value = hit
        if expires < time.monotonic():
            self._data.pop(key, None)
            return None
        return value

    def set(self, key: Hashable, value: Any, ttl: float) -> None:
        if len(self._data) >= self._max:
            # Drop the entry closest to expiry; fine at this size.
            oldest = min(self._data, key=lambda k: self._data[k][0])
            self._data.pop(oldest, None)
        self._data[key] = (time.monotonic() + ttl, value)


def raise_for_status(source: str, response: httpx.Response) -> None:
    if response.is_success:
        return
    code = response.status_code
    if code == 429:
        raise RateLimited(source, response.headers.get("Retry-After"))
    if code in (401, 403):
        raise UpstreamError(
            f"{source} rejected the credentials (HTTP {code}). Check the key/token "
            "and that you are subscribed to this product."
        )
    if code == 404:
        raise UpstreamError(f"{source} returned 404 for {response.request.url.path}.")
    # The body is the upstream's own error page: for the log, not the caller.
    log.warning("%s error HTTP %d: %s", source, code, response.text[:200].strip() or "no body")
    raise UpstreamError(f"{source} error (HTTP {code}).")


def json_body(source: str, response: httpx.Response) -> dict[str, Any]:
    """Parse a JSON object body, mapping junk (e.g. an HTML error page) to UpstreamError."""
    if not response.content:
        return {}
    try:
        data = response.json()
    except ValueError as exc:
        raise UpstreamError(
            f"{source} sent a response that isn't JSON; try again shortly."
        ) from exc
    if not isinstance(data, dict):
        raise UpstreamError(f"{source} sent an unexpected response.")
    return data
