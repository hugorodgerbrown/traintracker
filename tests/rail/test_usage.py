"""Counting Darwin requests against the allowance."""

from __future__ import annotations

import os
from datetime import date, timedelta
from io import StringIO
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest
import respx
from django.core.management import call_command
from mcp import Client

from apps.rail import server
from apps.rail import usage as usage_module
from apps.rail.config import DARWIN_DEPARTURES_URL, Settings
from apps.rail.darwin import DarwinClient
from apps.rail.server import darwin_usage
from apps.rail.usage import DailyBudget, DarwinUsage, Usage

from .test_clients import load
from .test_server import call, connect

TODAY = date(2026, 9, 29)


class Clock:
    """A clock the test moves by hand."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        """Return the time the test has set."""
        return self.now


def counter(clock: Clock | None = None, dsn: str | None = None) -> DarwinUsage:
    """A usage counter in this test's schema, on TODAY."""
    return DarwinUsage(
        dsn or os.environ["DATABASE_URL"],
        os.environ["USAGE_SCHEMA"],
        clock=clock or Clock(),
        today=lambda: TODAY,
    )


def stored() -> dict[tuple[date, str], int]:
    """The counts written to the database, by day and product."""
    with psycopg.connect(os.environ["DATABASE_URL"]) as con:
        rows = con.execute(
            f"SELECT day, product, requests FROM {os.environ['USAGE_SCHEMA']}.darwin_requests"
        ).fetchall()
    return {(day, product): requests for day, product, requests in rows}


async def test_counts_are_written_in_batches() -> None:
    """Counts are written every FLUSH_AFTER requests, or FLUSH_SECONDS."""
    clock = Clock()
    usage = counter(clock)
    assert usage.read() == Usage(0)  # before anything has been stored
    for _ in range(usage_module.FLUSH_AFTER - 1):
        usage.count("departures")
        await usage.flush()
    # Not yet written, but already counted.
    assert usage.read().requests == usage_module.FLUSH_AFTER - 1
    with pytest.raises(psycopg.errors.Error):
        stored()
    usage.count("service")
    await usage.flush()
    assert stored() == {(TODAY, "departures"): 49, (TODAY, "service"): 1}

    # A few requests don't wait for a full batch for ever.
    usage.count("departures")
    await usage.flush()
    assert stored()[(TODAY, "departures")] == 49
    clock.now += usage_module.FLUSH_SECONDS
    await usage.flush()
    assert stored()[(TODAY, "departures")] == 50
    assert usage.read() == Usage(51, {"departures": 50, "service": 1})


def test_the_daily_budget_stops_at_its_limit_and_starts_again_the_next_day() -> None:
    """The budget refuses past its limit until the next day."""
    day = TODAY
    budget = DailyBudget(3, spent=1, today=lambda: day)  # one was sent before a restart
    assert [budget.take() for _ in range(3)] == [True, True, False]
    day += timedelta(days=1)
    assert budget.take()


async def test_the_budget_resumes_from_todays_count() -> None:
    """After a restart the budget starts from today's count."""
    usage = counter()
    assert usage.spent_today() == 0  # before anything has been stored
    usage._pending[(TODAY - timedelta(days=1), "departures")] = 40
    usage.count("departures")
    usage.count("service")
    await usage.flush(force=True)
    usage.count("departures")  # counted, not yet written
    assert usage.spent_today() == 3


@respx.mock
async def test_past_the_daily_limit_live_tools_answer_with_booked_times(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Past the daily limit, live tools answer from the timetable."""
    monkeypatch.setenv("DARWIN_DAILY_LIMIT", "1")
    async with connect(tmp_path, monkeypatch) as client:
        route = respx.get(url__startswith=DARWIN_DEPARTURES_URL).mock(
            return_value=httpx.Response(200, json=load("darwin_departures_LST.json"))
        )
        live = await call(client, "live_departures", station="Liverpool Street")
        booked = await call(client, "live_departures", station="Marks Tey")
    assert live["source"] == "darwin"
    assert booked["source"] == "timetable"
    assert booked["messages"][0].startswith("This server has used today's allowance")
    assert booked["messages"][1] == "Showing booked times instead."
    assert route.call_count == 1


async def test_shutdown_writes_what_is_waiting() -> None:
    """A forced flush writes what is waiting, and nothing twice."""
    usage = counter()
    usage.count("arrivals")
    await usage.flush(force=True)
    assert stored() == {(TODAY, "arrivals"): 1}
    await usage.flush(force=True)  # nothing waiting: nothing to do
    assert stored() == {(TODAY, "arrivals"): 1}


async def test_usage_is_the_last_28_days() -> None:
    """Usage is the last 28 days, and older rows are deleted."""
    usage = counter()
    usage.count("departures")
    await usage.flush(force=True)
    days = {27: 10, 28: 100, 59: 1000, 61: 10000}
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as con:
        for ago, requests in days.items():
            con.execute(
                f"INSERT INTO {os.environ['USAGE_SCHEMA']}.darwin_requests VALUES (%s, %s, %s)",
                (TODAY - timedelta(days=ago), "departures", requests),
            )
    assert usage.read().requests == 11  # today and 27 days ago
    usage.count("departures")
    await usage.flush(force=True)
    # Rows too old to matter are deleted as new ones are written.
    assert TODAY - timedelta(days=61) not in {day for day, _ in stored()}
    assert TODAY - timedelta(days=59) in {day for day, _ in stored()}


async def test_a_warning_at_70_and_90_percent(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A warning is logged once at 70% and once at 90%."""
    monkeypatch.setattr(usage_module, "LIMIT", 10)
    usage = counter()

    async def use(requests: int) -> list[str]:
        caplog.clear()
        for _ in range(requests):
            usage.count("departures")
        await usage.flush(force=True)
        return [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]

    assert await use(6) == []
    seventy = await use(1)
    assert len(seventy) == 1
    assert "passed 70%" in seventy[0]
    assert "7 requests" in seventy[0]
    assert await use(1) == []  # said once
    ninety = await use(1)
    assert len(ninety) == 1
    assert "passed 90%" in ninety[0]
    assert await use(1) == []


async def test_a_failed_write_is_logged_and_retried(caplog: pytest.LogCaptureFixture) -> None:
    """A failed write is logged and its counts kept for the next."""
    broken = counter(dsn="postgresql://nobody:nothing@127.0.0.1:1/none")
    broken.count("departures")
    await broken.flush(force=True)  # does not raise
    assert "Darwin usage not recorded" in caplog.text
    assert broken.read_pending() == 1
    broken.dsn = os.environ["DATABASE_URL"]
    await broken.flush(force=True)
    assert stored() == {(TODAY, "departures"): 1}


async def test_a_failed_first_write_still_creates_the_table_next_time(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A rolled-back first write creates the table the next time."""
    usage = counter()
    usage.count("departures")

    def fails(_con: object) -> Usage:
        raise psycopg.OperationalError("connection lost")

    # The table is created and the count written, then the transaction fails
    # and Postgres rolls all of it back.
    with monkeypatch.context() as patch:
        patch.setattr(usage, "_stored", fails)
        await usage.flush(force=True)
    assert "Darwin usage not recorded" in caplog.text
    with pytest.raises(psycopg.errors.Error):
        stored()
    await usage.flush(force=True)
    assert stored() == {(TODAY, "departures"): 1}  # created this time, counted once


def test_any_darwin_product_is_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Usage is counted if any Darwin product is configured, not in demo mode."""
    for name in ("DARWIN_API_KEY", "DARWIN_SERVICE_API_KEY", "DARWIN_ARRIVALS_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert darwin_usage(Settings.from_env()) is None  # nothing can reach Darwin
    # Each product has its own key, and spends the allowance whichever it is.
    monkeypatch.setenv("DARWIN_ARRIVALS_API_KEY", "arrivals-key")
    monkeypatch.setenv("DARWIN_ARRIVALS_URL", "https://darwin.test/arrivals")
    assert darwin_usage(Settings.from_env()) is not None
    monkeypatch.delenv("DARWIN_ARRIVALS_API_KEY")
    monkeypatch.setenv("DARWIN_SERVICE_API_KEY", "service-key")
    assert darwin_usage(Settings.from_env()) is not None
    monkeypatch.setenv("TRAINTRACKER_DEMO", "1")
    assert darwin_usage(Settings.from_env()) is None


@respx.mock
async def test_arrivals_alone_are_counted_and_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, settings: Any
) -> None:
    """A server with only an arrivals key counts and reports them."""
    settings.DEBUG = True  # data_status reports usage only to whoever runs the server
    async with connect(tmp_path, monkeypatch) as client:
        # `connect` sets a departures key; this server is to have arrivals only.
        monkeypatch.delenv("DARWIN_API_KEY")
        monkeypatch.setenv("DARWIN_ARRIVALS_API_KEY", "arrivals-key")
        monkeypatch.setenv("DARWIN_ARRIVALS_URL", "https://darwin.test/arrivals")
    async with Client(server.mcp) as client:
        respx.get("https://darwin.test/arrivals/GetArrBoardWithDetails/LST").mock(
            return_value=httpx.Response(200, json=load("darwin_departures_LST.json"))
        )
        await call(client, "live_arrivals", station="Liverpool Street")
        out = await call(client, "data_status")
    assert out["darwin_live_departures"] == "missing DARWIN_API_KEY"
    assert out["darwin_usage"]["by_product"] == {"arrivals": 1}


async def test_a_hosted_server_keeps_its_usage_and_its_setup_to_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, settings: Any
) -> None:
    """Outside DEBUG, data_status leaves out usage and internals."""
    settings.DEBUG = True
    async with connect(tmp_path, monkeypatch) as client:
        own = await call(client, "data_status")
    settings.DEBUG = False
    async with connect(tmp_path, monkeypatch) as client:
        hosted = await call(client, "data_status")
    assert "darwin_usage" in own
    assert "schema" in own["network_rail_timetable"]
    # Anyone can sign in to a hosted server: how much of the allowance is left,
    # and how the server is set up inside, are for whoever runs it.
    assert "darwin_usage" not in hosted
    assert not {"schema", "last_refresh_error"} & set(hosted["network_rail_timetable"])
    assert hosted["network_rail_timetable"]["public_schedules"] != "0"


@respx.mock
async def test_only_requests_sent_to_darwin_are_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cached answers and refused requests are not counted."""
    monkeypatch.setenv("DARWIN_API_KEY", "darwin-key")
    usage = counter()
    respx.get(f"{DARWIN_DEPARTURES_URL}/GetDepBoardWithDetails/LST").mock(
        return_value=httpx.Response(200, json=load("darwin_departures_LST.json"))
    )
    respx.get(f"{DARWIN_DEPARTURES_URL}/GetDepBoardWithDetails/MKT").mock(
        return_value=httpx.Response(503)
    )
    async with httpx.AsyncClient() as http:
        darwin = DarwinClient(Settings.from_env(), http, usage)
        await darwin.board("LST", "departures")
        await darwin.board("LST", "departures")  # answered from the cache
        with pytest.raises(Exception, match="503"):
            await darwin.board("MKT", "departures")  # a failed request still counts
    assert usage.read() == Usage(2, {"departures": 2})


@respx.mock
async def test_data_status_and_the_status_command_report_usage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, settings: Any
) -> None:
    """data_status and rail_status both report the count."""
    settings.DEBUG = True  # data_status reports usage only to whoever runs the server
    async with connect(tmp_path, monkeypatch) as client:
        respx.get(f"{DARWIN_DEPARTURES_URL}/GetDepBoardWithDetails/LST").mock(
            return_value=httpx.Response(200, json=load("darwin_departures_LST.json"))
        )
        await call(client, "live_departures", station="Liverpool Street")
        out = await call(client, "data_status")
    assert out["darwin_usage"] == {
        "window": "rolling 28 days",
        "requests": 1,
        "by_product": {"departures": 1},
        "limit": 5_000_000,
        "percent_used": 0.0,
    }
    # Closing the server wrote the count, so another process sees it.
    status = StringIO()
    call_command("rail_status", stdout=status)
    assert "Darwin usage: 1 request in the last 28 days, 0.0% of 5,000,000" in status.getvalue()


async def test_demo_mode_counts_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Demo mode counts no Darwin requests."""
    monkeypatch.setenv("TRAINTRACKER_DEMO", "1")
    monkeypatch.setenv("TRAINTRACKER_DATA_DIR", str(tmp_path))
    async with Client(server.mcp) as client:
        await call(client, "live_departures", station="Liverpool Street")
        out = await call(client, "data_status")
    assert "darwin_usage" not in out
