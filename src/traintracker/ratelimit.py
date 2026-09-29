"""Per-account rate limiting for tool calls.

One Darwin key serves every user, so one account must not be able to spend the
whole allowance. Each account gets a token bucket: RATE_LIMIT_BURST calls at
once, refilled at RATE_LIMIT_PER_MINUTE. Buckets live in memory, which is
enough for one instance; a restart hands everyone a full bucket.

The limit is applied inside MCP rather than as an HTTP 429, so a limited call
comes back as a tool error the model can read and relay.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.types import CallToolResult, TextContent

MAX_KEYS = 10_000


@dataclass
class _Bucket:
    tokens: float
    updated: float


class RateLimiter:
    def __init__(
        self,
        per_minute: int,
        burst: int,
        clock: Callable[[], float] = time.monotonic,
        max_keys: int = MAX_KEYS,
    ) -> None:
        self.rate = per_minute / 60  # tokens per second
        self.burst = max(1, burst)
        self.clock = clock
        self.max_keys = max_keys
        self._buckets: dict[str, _Bucket] = {}

    def take(self, key: str) -> int:
        """Spend one call for `key`. Returns 0 if allowed, else the seconds to wait."""
        now = self.clock()
        bucket = self._buckets.get(key)
        if bucket is None:
            self._prune(now)
            bucket = self._buckets[key] = _Bucket(float(self.burst), now)
        bucket.tokens = min(self.burst, bucket.tokens + (now - bucket.updated) * self.rate)
        bucket.updated = now
        if bucket.tokens >= 1:
            bucket.tokens -= 1
            return 0
        return max(1, math.ceil((1 - bucket.tokens) / self.rate))

    def _prune(self, now: float) -> None:
        """Keep memory bounded: forget refilled buckets, then the longest idle."""
        if len(self._buckets) < self.max_keys:
            return
        refill = self.burst / self.rate
        self._buckets = {k: b for k, b in self._buckets.items() if now - b.updated < refill}
        while len(self._buckets) >= self.max_keys:
            del self._buckets[min(self._buckets, key=lambda k: self._buckets[k].updated)]


def account_key(request: Any) -> str | None:
    """Who a request is from: the signed-in account, else the OAuth client.

    None for stdio (no request) and for a request nobody authenticated, neither
    of which is limited.
    """
    scope = getattr(request, "scope", None)
    user = scope.get("user") if scope else None
    if not isinstance(user, AuthenticatedUser):
        return None
    token = user.access_token
    return token.subject or token.client_id


class RateLimitMiddleware:
    """MCP middleware that answers a tool call over the limit with a tool error."""

    def __init__(self, limiter: RateLimiter) -> None:
        self.limiter = limiter

    async def __call__(
        self, ctx: ServerRequestContext[Any, Any], call_next: CallNext
    ) -> HandlerResult:
        if ctx.method == "tools/call" and (key := account_key(ctx.request)) is not None:
            wait = self.limiter.take(key)
            if wait:
                return CallToolResult(
                    content=[TextContent(type="text", text=limited(wait))], is_error=True
                )
        return await call_next(ctx)


def limited(wait: int) -> str:
    return f"Too many requests. Try again in {wait} second{'' if wait == 1 else 's'}."
