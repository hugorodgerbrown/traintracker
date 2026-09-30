"""Per-account rate limiting for tool calls.

One Darwin key serves every user, so one account must not be able to spend the
whole allowance. Each account gets a token bucket: RATE_LIMIT_BURST calls at
once, refilled at RATE_LIMIT_PER_MINUTE. Buckets live in memory, which is
enough for one instance; a restart hands everyone a full bucket.

A call pays for one request to Darwin. A call that pages through a board, or
looks up each leg of a journey, makes more, and each further request is charged
to the same bucket: the call is not refused part-way, the bucket goes into debt
and the account's next calls wait for it to refill. Without that, one call could
cost a dozen requests and the per-minute rate would say little about the
allowance spent.

The limit is applied inside MCP rather than as an HTTP 429, so a limited call
comes back as a tool error the model can read and relay.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from contextvars import ContextVar
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
        per_minute: float,
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
        bucket = self._bucket(key)
        if bucket.tokens >= 1:
            bucket.tokens -= 1
            return 0
        return max(1, math.ceil((1 - bucket.tokens) / self.rate))

    def charge(self, key: str) -> None:
        """Spend one more for a call already allowed. Never refuses: the bucket
        may go into debt, by no more than a full bucket, and later calls wait."""
        bucket = self._bucket(key)
        bucket.tokens = max(-float(self.burst), bucket.tokens - 1)

    def _bucket(self, key: str) -> _Bucket:
        """The bucket for `key`, topped up to now."""
        now = self.clock()
        bucket = self._buckets.get(key)
        if bucket is None:
            self._prune(now)
            bucket = self._buckets[key] = _Bucket(float(self.burst), now)
        bucket.tokens = min(self.burst, bucket.tokens + (now - bucket.updated) * self.rate)
        bucket.updated = now
        return bucket

    def _prune(self, now: float) -> None:
        """Keep memory bounded: forget refilled buckets, then the longest idle."""
        if len(self._buckets) < self.max_keys:
            return
        refill = 2 * self.burst / self.rate  # from the deepest debt to full
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


@dataclass
class _Call:
    """The tool call in progress, so that what it fetches can be charged to it."""

    limiter: RateLimiter
    key: str
    paid: int = 1  # upstream requests the call's own token covers


_call: ContextVar[_Call | None] = ContextVar("rate_limited_call", default=None)


def charge_upstream() -> None:
    """Count one request sent upstream against the account making this tool call.

    The first is covered by the call itself. Does nothing outside a limited
    call (stdio, or the limit turned off).
    """
    call = _call.get()
    if call is None:
        return
    if call.paid:
        call.paid -= 1
    else:
        call.limiter.charge(call.key)


class RateLimitMiddleware:
    """MCP middleware that answers a tool call over the limit with a tool error."""

    def __init__(self, limiter: RateLimiter) -> None:
        self.limiter = limiter

    async def __call__(
        self, ctx: ServerRequestContext[Any, Any], call_next: CallNext
    ) -> HandlerResult:
        if ctx.method != "tools/call" or (key := account_key(ctx.request)) is None:
            return await call_next(ctx)
        wait = self.limiter.take(key)
        if wait:
            return CallToolResult(
                content=[TextContent(type="text", text=limited(wait))], is_error=True
            )
        current = _call.set(_Call(self.limiter, key))
        try:
            return await call_next(ctx)
        finally:
            _call.reset(current)


def limited(wait: int) -> str:
    return f"Too many requests. Try again in {wait} second{'' if wait == 1 else 's'}."
