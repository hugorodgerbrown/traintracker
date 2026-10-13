"""The per-account rate limiter: the bucket itself, and what a call is charged.

How a limited call reaches the model over /mcp, and what a journey plan costs,
are in tests/mcp/test_mcp.py.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import respx
from mcp.types import CallToolResult

from apps.mcp import tools
from apps.rail import ratelimit, runtime, server
from apps.rail.config import DARWIN_DEPARTURES_URL
from apps.rail.ratelimit import PLAN_COST, RateLimiter, charge_upstream, charged, limited
from apps.rail.timetable import Timetable, build

from . import feedgen
from .conftest import timetable_db
from .test_server import _darwin_board, _darwin_svc, _Frozen, connect


class Clock:
    """A clock the test moves by hand."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        """Return the time the test has set."""
        return self.now


def test_bucket_empties_reports_the_wait_and_refills() -> None:
    """An empty bucket says how long to wait, and refills to its size and no further."""
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
    """One account emptying its bucket leaves another's full."""
    limiter = RateLimiter(per_minute=1, burst=1, clock=Clock())
    assert limiter.take("a") == 0
    assert limiter.take("a") == 60
    assert limiter.take("b") == 0


def test_idle_buckets_are_forgotten() -> None:
    """Past max_keys, refilled buckets go first, then the longest idle."""
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


def test_a_call_can_count_as_several() -> None:
    """A call costing several takes them all, but never more than a full bucket."""
    clock = Clock()
    limiter = RateLimiter(per_minute=60, burst=4, clock=clock)  # one a second
    assert limiter.take("a", 3) == 0
    assert limiter.take("a", 3) == 2
    assert limiter.take("a") == 0
    # A cost above the bucket's size is the whole bucket, not a call that can never be made.
    clock.now += 60
    assert limiter.take("a", 10) == 0


def test_a_charge_puts_the_bucket_in_debt_but_no_deeper_than_a_full_one() -> None:
    """Charges are never refused; the debt they leave is at most a bucket deep."""
    clock = Clock()
    limiter = RateLimiter(per_minute=60, burst=2, clock=clock)  # one a second
    assert limiter.take("a") == 0
    for _ in range(10):
        limiter.charge("a")
    assert limiter.take("a") == 3  # from two in debt back up to one
    clock.now += 3
    assert limiter.take("a") == 0


def test_a_call_covers_its_first_upstream_request_and_is_charged_for_the_rest() -> None:
    """Inside a call, the first upstream request is free and each further one costs one."""
    limiter = RateLimiter(per_minute=60, burst=5, clock=Clock())
    charge_upstream()  # no call in progress (the limit is off): nothing to charge
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


async def test_charged_charges_the_key_only_while_the_call_runs() -> None:
    """charged() returns the call's result, and charges nothing once it has ended."""
    limiter = RateLimiter(per_minute=60, burst=5, clock=Clock())
    limiter.take("a")

    async def fetch_three(answer: int) -> int:
        """Stand in for a tool that sends three requests upstream."""
        for _ in range(3):
            charge_upstream()
        return answer

    assert await charged(limiter, "a", fetch_three, 7) == 7
    assert limiter._buckets["a"].tokens == 2  # one taken, two charged
    charge_upstream()
    assert limiter._buckets["a"].tokens == 2


def test_the_message_reads_as_a_sentence() -> None:
    """The wait is in whole seconds, singular for one."""
    assert limited(1) == "Too many requests. Try again in 1 second."
    assert limited(12) == "Too many requests. Try again in 12 seconds."


@pytest.fixture
def fresh_limiter() -> Iterator[None]:
    """Forget the process's cached limiter before and after the test."""
    tools.limiter.cache_clear()
    yield
    tools.limiter.cache_clear()


@pytest.mark.usefixtures("fresh_limiter")
def test_the_limiter_follows_the_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """RATE_LIMIT_PER_MINUTE and RATE_LIMIT_BURST size the buckets; 0 turns them off."""
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "6")
    monkeypatch.setenv("RATE_LIMIT_BURST", "3")
    buckets = tools.limiter()
    assert buckets is not None
    assert (buckets.rate, buckets.burst) == (0.1, 3)
    assert tools.limiter() is buckets  # one set of buckets for the process
    tools.limiter.cache_clear()
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "0")
    assert tools.limiter() is None


@respx.mock
async def test_a_call_that_pages_through_a_board_pays_for_each_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each Darwin request after a call's first is charged to the account's bucket."""
    monkeypatch.setattr(server, "datetime", _Frozen)
    # Ten services is a full page, so the board is read page by page: three
    # requests to Darwin for the one call.
    first = [_darwin_svc(f"13:{m:02d}", "2") for m in range(1, 11)]
    second = [_darwin_svc(f"13:{m:02d}", "2") for m in range(10, 20)]
    route = respx.get(f"{DARWIN_DEPARTURES_URL}/GetDepBoardWithDetails/LST").mock(
        side_effect=lambda request: httpx.Response(
            200, json=_darwin_board(first if request.url.params["timeOffset"] == "0" else second)
        )
    )
    limiter = RateLimiter(per_minute=60, burst=10, clock=Clock())
    async with connect(tmp_path, monkeypatch):
        assert limiter.take("1") == 0
        board = await charged(
            limiter,
            "1",
            server.mcp.call_tool,
            "platform_departures",
            {"station": "LST", "platform": "9"},
        )
    assert isinstance(board, CallToolResult)
    assert board.structured_content is not None
    assert board.structured_content["services"] == []
    assert route.call_count == 3
    assert limiter._buckets["1"].tokens == 10 - 1 - 2


@respx.mock
async def test_a_journey_pays_for_looking_up_each_leg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A plan's live times cost a Darwin request a leg, charged on top of PLAN_COST."""
    monkeypatch.setattr(server, "datetime", _Frozen)
    route = respx.get(url__startswith=DARWIN_DEPARTURES_URL).mock(return_value=httpx.Response(503))
    limiter = RateLimiter(per_minute=60, burst=10, clock=Clock())
    async with connect(tmp_path, monkeypatch):
        assert limiter.take("1", PLAN_COST) == 0
        await charged(
            limiter,
            "1",
            server.mcp.call_tool,
            "plan_journey",
            {"origin": "LST", "destination": "SUY"},
        )
    # A change at Marks Tey: the board for each leg is asked for.
    assert route.call_count >= 2
    assert limiter._buckets["1"].tokens == 10 - PLAN_COST - (route.call_count - 1)


@pytest.fixture
def train_tools_on_the_loop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A timetable and a Darwin key for the train tools' own event loop, stopped after."""
    for key in ("NR_USERNAME", "NR_PASSWORD", "DARWIN_ARRIVALS_API_KEY", "DARWIN_ARRIVALS_URL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DARWIN_API_KEY", "darwin-key")
    monkeypatch.setenv("TRAINTRACKER_DATA_DIR", str(tmp_path))
    build(feedgen.feed(), timetable_db(), today=date(2026, 9, 26))
    Timetable.clear_caches()
    try:
        yield
    finally:
        runtime.stop()


@respx.mock
@pytest.mark.usefixtures("train_tools_on_the_loop")
def test_an_account_s_tool_call_is_charged_for_what_it_fetches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Through /mcp's own path, a board that pages leaves the account's bucket in debt."""
    monkeypatch.setattr(server, "datetime", _Frozen)
    first = [_darwin_svc(f"13:{m:02d}", "2") for m in range(1, 11)]
    second = [_darwin_svc(f"13:{m:02d}", "2") for m in range(10, 20)]
    route = respx.get(f"{DARWIN_DEPARTURES_URL}/GetDepBoardWithDetails/LST").mock(
        side_effect=lambda request: httpx.Response(
            200, json=_darwin_board(first if request.url.params["timeOffset"] == "0" else second)
        )
    )
    buckets = RateLimiter(per_minute=60, burst=2, clock=Clock())
    monkeypatch.setattr(tools, "limiter", lambda: buckets)
    account = SimpleNamespace(pk=1)
    board = tools._run_train_tool(
        account, "platform_departures", {"station": "LST", "platform": "9"}
    )
    assert board["services"] == []
    assert route.call_count == 3
    # Two requests beyond the call's own: a bucket of two is emptied, and more.
    assert buckets._buckets["1"].tokens == -1
    with pytest.raises(tools.ToolError, match="Try again in 2 seconds"):
        tools._run_train_tool(account, "find_station", {"query": "sudbury"})
    # Another account pays nothing for it.
    assert tools._run_train_tool(SimpleNamespace(pk=2), "find_station", {"query": "sudbury"})
