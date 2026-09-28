"""Demo mode: generated timetable and in-process Darwin."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from mcp import Client

from traintracker import demo, server
from traintracker.config import UK_TZ, Settings
from traintracker.darwin import DarwinClient
from traintracker.timetable import Timetable, build

from .test_server import call, drop_timetable

# A Friday; the demo timetable is built relative to this date.
TODAY = date(2026, 10, 2)
NOON = datetime(2026, 10, 2, 12, 50, tzinfo=UK_TZ)


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("TRAINTRACKER_DEMO", "1")
    monkeypatch.setenv("TRAINTRACKER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("NR_USERNAME", "real-user")
    monkeypatch.setenv("NR_PASSWORD", "real-password")
    s = Settings.from_env()
    build(demo.feed(TODAY), s.timetable_db, today=TODAY)
    Timetable.clear_caches()
    return s


def test_demo_settings_use_no_real_accounts(settings: Settings) -> None:
    assert settings.demo
    assert settings.timetable_db.schema.endswith("_demo")
    assert settings.has_darwin and settings.has_darwin_arrivals
    assert not settings.has_nr


def test_timetable_runs_every_route(settings: Settings) -> None:
    tt = Timetable.open(settings.timetable_db)
    assert tt.meta["valid_to"] == (TODAY + timedelta(days=demo.DAYS_AHEAD)).isoformat()
    for crs in set(demo.TIPLOCS.values()):
        assert tt.trips_at(crs, TODAY, 12 * 60, 14 * 60), crs
    # Sundays start later.
    sunday = TODAY + timedelta(days=2)
    assert tt.trips_at("MKT", TODAY, 6 * 60, 7 * 60)
    assert not tt.trips_at("MKT", sunday, 6 * 60, 7 * 60)


def test_running_mixes_delays_and_cancellations(settings: Settings) -> None:
    trips = Timetable.open(settings.timetable_db).day_trips(TODAY)
    runs = [demo.running(t) for t in trips]
    assert any(r.cancelled for r in runs)
    assert any(r.delay is None for r in runs)
    assert any(r.delay for r in runs)
    assert sum(r.delay == 0 and not r.cancelled for r in runs) > len(runs) / 2
    assert runs == [demo.running(t) for t in trips]  # same answer every call


async def test_darwin_board_and_service(settings: Settings) -> None:
    async with httpx.AsyncClient(transport=demo.transport(settings, lambda: NOON)) as http:
        darwin = DarwinClient(settings, http)
        board = await darwin.board("LST", "departures", filter_crs="MKT", rows=5)
        assert board.source == "darwin"
        assert board.messages == [demo.DEMO_NOTE]
        assert board.services and all(
            s.scheduled and s.scheduled[3:] == "30" for s in board.services
        )
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
    async with httpx.AsyncClient(transport=demo.transport(settings, lambda: NOON)) as http:
        board = await DarwinClient(settings, http).board("CBG", "departures", rows=30)
        times = [s.scheduled or "" for s in board.services]
        assert len(times) > 10  # more than one 10-row page
        assert times == sorted(times)


async def test_tools_in_demo_mode(settings: Settings) -> None:
    drop_timetable(settings.timetable_db)  # the server builds one for the real date
    async with Client(server.mcp) as client:
        status = await call(client, "data_status")
        assert "demo_mode" in status and "SUY" in status["demo_stations"]

        board = await call(client, "live_departures", station="Cambridge")
        assert board["source"] == "darwin"
        assert demo.DEMO_NOTE in board["messages"]

        day = (datetime.now(UK_TZ).date() + timedelta(days=1)).isoformat()
        tt = await call(client, "timetable", station="Marks Tey", to="SUY", date=day, time="13:00")
        assert tt["messages"][0] == demo.DEMO_NOTE
        assert tt["services"][0]["scheduled"] == "13:35"

        plan = await call(
            client, "plan_journey", origin="Cambridge", destination="SUY", date=day, time="09:00"
        )
        assert plan["journeys"]
        assert plan["journeys"][0]["legs"][-1]["alight_at"]["crs"] == "SUY"
