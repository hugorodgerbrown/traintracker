"""Counting Darwin requests against the allowance."""

from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path

import httpx
import psycopg
import pytest
import respx

from traintracker import usage as usage_module
from traintracker.config import DARWIN_DEPARTURES_URL, Settings
from traintracker.darwin import DarwinClient
from traintracker.server import main
from traintracker.usage import DarwinUsage, Usage

from .test_clients import load
from .test_server import call, connect

TODAY = date(2026, 9, 29)


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def counter(clock: Clock | None = None, dsn: str | None = None) -> DarwinUsage:
    return DarwinUsage(
        dsn or os.environ["DATABASE_URL"],
        os.environ["USAGE_SCHEMA"],
        clock=clock or Clock(),
        today=lambda: TODAY,
    )


def stored() -> dict[tuple[date, str], int]:
    with psycopg.connect(os.environ["DATABASE_URL"]) as con:
        rows = con.execute(
            f"SELECT day, product, requests FROM {os.environ['USAGE_SCHEMA']}.darwin_requests"
        ).fetchall()
    return {(day, product): requests for day, product, requests in rows}


async def test_counts_are_written_in_batches() -> None:
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


async def test_shutdown_writes_what_is_waiting() -> None:
    usage = counter()
    usage.count("arrivals")
    await usage.flush(force=True)
    assert stored() == {(TODAY, "arrivals"): 1}
    await usage.flush(force=True)  # nothing waiting: nothing to do
    assert stored() == {(TODAY, "arrivals"): 1}


async def test_usage_is_the_last_28_days() -> None:
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
    assert len(seventy) == 1 and "passed 70%" in seventy[0] and "7 requests" in seventy[0]
    assert await use(1) == []  # said once
    ninety = await use(1)
    assert len(ninety) == 1 and "passed 90%" in ninety[0]
    assert await use(1) == []


async def test_a_failed_write_is_logged_and_retried(caplog: pytest.LogCaptureFixture) -> None:
    broken = counter(dsn="postgresql://nobody:nothing@127.0.0.1:1/none")
    broken.count("departures")
    await broken.flush(force=True)  # does not raise
    assert "Darwin usage not recorded" in caplog.text
    assert broken.read_pending() == 1
    broken.dsn = os.environ["DATABASE_URL"]
    await broken.flush(force=True)
    assert stored() == {(TODAY, "departures"): 1}


@respx.mock
async def test_only_requests_sent_to_darwin_are_counted(monkeypatch: pytest.MonkeyPatch) -> None:
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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
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
    main(["status"])
    assert "Darwin usage: 1 request in the last 28 days, 0.0% of 5,000,000" in (
        capsys.readouterr().err
    )


async def test_demo_mode_counts_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRAINTRACKER_DEMO", "1")
    monkeypatch.setenv("TRAINTRACKER_DATA_DIR", str(tmp_path))
    from mcp import Client

    from traintracker import server

    async with Client(server.mcp) as client:
        await call(client, "live_departures", station="Liverpool Street")
        out = await call(client, "data_status")
    assert "darwin_usage" not in out
