"""Fixed-window rate limits kept in the cache.

Small on purpose: a counter per key per window, enough to stop someone
mailing a stranger a hundred sign-in links or guessing codes. The cache is
per process unless CACHES points at a shared one (Redis, database), so with
several workers each counts on its own; that is still a hard ceiling.
"""

from __future__ import annotations

import hashlib
import time

from django.conf import settings
from django.core.cache import cache
from django.http import HttpRequest


def client_ip(request: HttpRequest) -> str:
    """Return the address the request came from.

    Behind ``RATE_LIMIT_PROXY_COUNT`` trusted proxies the client is that many
    entries from the end of ``X-Forwarded-For``: anything before them was
    written by the client and can't be trusted.
    """
    proxies = settings.RATE_LIMIT_PROXY_COUNT
    if proxies > 0:
        hops = [h.strip() for h in request.headers.get("X-Forwarded-For", "").split(",")]
        hops = [h for h in hops if h]
        if len(hops) >= proxies:
            return hops[-proxies]
    return str(request.META.get("REMOTE_ADDR", ""))


def over_limit(group: str, value: str, *, limit: int, window_seconds: int) -> bool:
    """Count one hit for ``value`` in ``group`` and return True once it passes ``limit``.

    ``value`` is hashed, so an email address or IP never appears in a cache key.
    """
    window = int(time.time() // window_seconds)
    digest = hashlib.sha256(value.encode()).hexdigest()[:32]
    key = f"ratelimit:{group}:{digest}:{window}"
    cache.add(key, 0, timeout=window_seconds)
    try:
        count = cache.incr(key)
    except ValueError:  # expired between add() and incr()
        cache.set(key, 1, timeout=window_seconds)
        count = 1
    return count > limit
