"""Demo mode: generated timetable and in-process Darwin."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from freezegun import freeze_time
from mcp import Client

from apps.rail import demo, server
from apps.rail.config import UK_TZ, Settings
from apps.rail.darwin import DarwinClient
from apps.rail.timetable import Timetable, build

from .test_server import call, drop_timetable

# A Friday; the demo timetable is built relative to this date.
TODAY = date(2026, 10, 2)
NOON = datetime(2026, 10, 2, 12, 50, tzinfo=UK_TZ)


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Demo settings, with real NR credentials set that it must not use."""
    monkeypatch.setenv("TRAINTRACKER_DEMO", "1")
    monkeypatch.setenv("TRAINTRACKER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("NR_USERNAME", "real-user")
    monkeypatch.setenv("NR_PASSWORD", "real-password")
    s = Settings.from_env()
    build(demo.feed(TODAY), s.timetable_db, today=TODAY)
    Timetable.clear_caches()
    return s


def test_demo_settings_use_no_real_accounts(settings: Settings) -> None:
    """Demo mode has its own schema and needs no accounts."""
    assert settings.demo
    assert settings.timetable_db.schema.endswith("_demo")
    assert settings.has_darwin
    assert settings.has_darwin_arrivals
    assert not settings.has_nr


def test_timetable_runs_every_route(settings: Settings) -> None:
    """The generated timetable serves every demo station, later on Sundays."""
    tt = Timetable.open(settings.timetable_db)
    assert tt.meta["valid_to"] == (TODAY + timedelta(days=demo.DAYS_AHEAD)).isoformat()
    for crs in set(demo.TIPLOCS.values()):
        assert tt.trips_at(crs, TODAY, 12 * 60, 14 * 60), crs
    # Sundays start later.
    sunday = TODAY + timedelta(days=2)
    assert tt.trips_at("MKT", TODAY, 6 * 60, 7 * 60)
    assert not tt.trips_at("MKT", sunday, 6 * 60, 7 * 60)


def test_running_mixes_delays_and_cancellations(settings: Settings) -> None:
    """Demo running mixes delays and cancellations, the same every call."""
    trips = Timetable.open(settings.timetable_db).day_trips(TODAY)
    runs = [demo.running(t) for t in trips]
    assert any(r.cancelled for r in runs)
    assert any(r.delay is None for r in runs)
    assert any(r.delay for r in runs)
    assert sum(r.delay == 0 and not r.cancelled for r in runs) > len(runs) / 2
    assert runs == [demo.running(t) for t in trips]  # same answer every call


async def test_darwin_board_and_service(settings: Settings) -> None:
    """The in-process Darwin answers boards and services from the timetable."""
    async with httpx.AsyncClient(transport=demo.transport(settings, lambda: NOON)) as http:
        darwin = DarwinClient(settings, http)
        board = await darwin.board("LST", "departures", filter_crs="MKT", rows=5)
        assert board.source == "darwin"
        assert board.messages == [demo.DEMO_NOTE]
        assert board.services
        assert all(s.scheduled and s.scheduled[3:] == "30" for s in board.services)
        assert all(s.destination[0].crs == "COL" for s in board.services)
        # Liverpool Street announces platforms only shortly before departure.
        assert all(s.platform is None for s in board.services if (s.scheduled or "") > "13:05")

        detail = await darwin.service(board.services[0].service_id.removeprefix("darwin:"))
        crs = [c.station.crs for c in detail.calling_points]
        assert crs == ["LST", "SRA", "SNF", "CHM", "WTM", "KEL", "MKT", "COL"]

        # One train an hour on the Sudbury branch.
        arrivals = await darwin.board("SUY", "arrivals")
        assert [s.origin[0].crs for s in arrivals.services] == ["MKT", "MKT"]


async def test_darwin_board_pages(settings: Settings) -> None:
    """The demo Darwin pages its boards as the real one does."""
    async with httpx.AsyncClient(transport=demo.transport(settings, lambda: NOON)) as http:
        board = await DarwinClient(settings, http).board("CBG", "departures", rows=30)
        times = [s.scheduled or "" for s in board.services]
        assert len(times) > 10  # more than one 10-row page
        assert times == sorted(times)


async def test_tools_in_demo_mode(settings: Settings) -> None:
    """Every tool answers in demo mode and says it is demo data."""
    drop_timetable(settings.timetable_db)  # the server builds one for the real date
    async with Client(server.mcp) as client:
        status = await call(client, "data_status")
        assert "demo_mode" in status
        assert "SUY" in status["demo_stations"]

        board = await call(client, "live_departures", station="Cambridge")
        assert board["source"] == "darwin"
        assert demo.DEMO_NOTE in board["messages"]

        day = (datetime.now(UK_TZ).date() + timedelta(days=1)).isoformat()
        tt = await call(client, "timetable", station="Marks Tey", to="SUY", date=day, time="13:00")
        assert tt["messages"][0] == demo.DEMO_NOTE
        assert tt["services"][0]["scheduled"] == "13:35"
        assert tt["services"][0]["trainline_url"] is None  # Trainline has no such train

        plan = await call(
            client, "plan_journey", origin="Cambridge", destination="SUY", date=day, time="09:00"
        )
        assert plan["journeys"]
        assert not any(leg["trainline_url"] for j in plan["journeys"] for leg in j["legs"])
        assert plan["journeys"][0]["legs"][-1]["alight_at"]["crs"] == "SUY"


async def test_journeys_with_a_change_for_a_board(settings: Settings) -> None:
    """A board to Sudbury from Liverpool Street: every train to Marks Tey and its connection."""
    drop_timetable(settings.timetable_db)  # the server builds one for the frozen date
    with freeze_time(NOON - timedelta(minutes=50), real_asyncio=True):
        async with Client(server.mcp):
            journeys = await server.public_changes("LST", "SUY")
            direct = await server.public_changes("LST", "COL")
    assert journeys
    assert [j.depart for j in journeys] == sorted(j.depart for j in journeys)
    for j in journeys:
        first, then = j.legs
        assert (first.board_at.crs, then.alight_at.crs) == ("LST", "SUY")
        assert first.alight_at.crs == then.board_at.crs == "MKT"
        # Within the board's two hours.
        assert datetime.fromisoformat(j.depart) <= NOON + timedelta(minutes=70)
    assert any(leg.depart_expected for j in journeys for leg in j.legs)  # live where Darwin has it
    assert direct == []  # Colchester has direct trains, which beat any with a change
