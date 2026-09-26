"""End-to-end tool tests through an in-process MCP client."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from mcp import Client

from traintracker import server
from traintracker.config import DARWIN_DEPARTURES_URL, UK_TZ
from traintracker.models import Journey, JourneyLeg, StationRef
from traintracker.timetable import Timetable, build

from . import feedgen
from .test_clients import load


@asynccontextmanager
async def connect(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Client]:
    for key in (
        "NR_USERNAME",
        "NR_PASSWORD",
        "RTT_ACCESS_TOKEN",
        "RTT_REFRESH_TOKEN",
        "DARWIN_ARRIVALS_API_KEY",
        "DARWIN_ARRIVALS_URL",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DARWIN_API_KEY", "darwin-key")
    monkeypatch.setenv("TRAINTRACKER_DATA_DIR", str(tmp_path))
    build(feedgen.feed(), tmp_path / "timetable.sqlite", today=date(2026, 9, 26))
    Timetable.clear_caches()
    async with Client(server.mcp) as c:
        yield c


async def call(client: Client, tool: str, **args: Any) -> Any:
    result = await client.call_tool(tool, args)
    assert not result.is_error, result.content
    return result.structured_content


async def error(client: Client, tool: str, **args: Any) -> str:
    result = await client.call_tool(tool, args)
    assert result.is_error
    return " ".join(getattr(c, "text", "") for c in result.content)


async def test_lists_all_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async with connect(tmp_path, monkeypatch) as client:
        names = {t.name for t in (await client.list_tools()).tools}
        assert names == {
            "find_station",
            "live_departures",
            "live_arrivals",
            "timetable",
            "service_details",
            "plan_journey",
            "data_status",
        }


async def test_find_station(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async with connect(tmp_path, monkeypatch) as client:
        out = await call(client, "find_station", query="sudbury")
        crs = [m["crs"] for m in out["result"]]
        assert {"SUY", "SDH", "SUD"} <= set(crs)


async def test_ambiguous_station_is_a_clear_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with connect(tmp_path, monkeypatch) as client:
        msg = await error(client, "timetable", station="sudbury", date="2026-10-02")
        assert "ambiguous" in msg and "SUY" in msg


@respx.mock
async def test_live_departures_via_darwin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async with connect(tmp_path, monkeypatch) as client:
        respx.get(f"{DARWIN_DEPARTURES_URL}/GetDepBoardWithDetails/LST").mock(
            return_value=httpx.Response(200, json=load("darwin_departures_LST.json"))
        )
        out = await call(client, "live_departures", station="Liverpool Street", to="Marks Tey")
        assert out["source"] == "darwin"
        assert len(out["services"]) == 3
        assert all(s["calling_points"] is None for s in out["services"])


async def test_timetable_board(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async with connect(tmp_path, monkeypatch) as client:
        out = await call(
            client,
            "timetable",
            station="Marks Tey",
            date="2026-10-02",
            time="13:00",
            to="SUY",
            window_minutes=120,
        )
        assert out["source"] == "timetable"
        assert [s["scheduled"] for s in out["services"]] == ["13:58", "14:58"]
        assert out["services"][0]["destination"][0]["crs"] == "SUY"
        assert out["services"][0]["operator"] == "Greater Anglia"


async def test_service_details_timetable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async with connect(tmp_path, monkeypatch) as client:
        out = await call(client, "service_details", service_id="tt:B00013:2026-10-02")
        assert [c["station"]["crs"] for c in out["calling_points"]] == ["MKT", "CWC", "BUE", "SUY"]
        msg = await error(client, "service_details", service_id="nonsense")
        assert "darwin:" in msg


async def test_plan_journey(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async with connect(tmp_path, monkeypatch) as client:
        out = await call(
            client,
            "plan_journey",
            origin="LST",
            destination="Sudbury (Suffolk)",
            date="2026-10-02",
            time="12:05",
            count=2,
        )
        first = out["journeys"][0]
        assert first["changes"] == 1
        assert first["arrive"].startswith("2026-10-02T14:23")
        assert out["origin"]["crs"] == "LST"


async def test_data_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async with connect(tmp_path, monkeypatch) as client:
        out = await call(client, "data_status")
        assert out["darwin_live_departures"] == "configured"
        assert out["realtime_trains"].startswith("not configured")
        assert out["network_rail_timetable"]["public_schedules"] != "0"


async def test_missing_timetable_message(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRAINTRACKER_DATA_DIR", str(tmp_path / "empty"))
    for key in ("NR_USERNAME", "NR_PASSWORD", "RTT_ACCESS_TOKEN", "RTT_REFRESH_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    Timetable.clear_caches()
    async with Client(server.mcp) as c:
        msg = await error(c, "plan_journey", origin="LST", destination="SUY")
    assert "No timetable yet" in msg


def _leg(dep: str, arr: str, dep_exp: str | None = None, arr_exp: str | None = None) -> JourneyLeg:
    return JourneyLeg(
        service_id="tt:X:2026-10-02",
        board_at=StationRef(name="A", crs="AAA"),
        alight_at=StationRef(name="B", crs="BBB"),
        destination="B",
        depart_scheduled=dep,
        arrive_scheduled=arr,
        depart_expected=dep_exp,
        arrive_expected=arr_exp,
    )


def test_connection_risk() -> None:
    base = datetime(2026, 10, 2, 13, 0, tzinfo=UK_TZ)

    def iso(minutes: int) -> str:
        return (base + timedelta(minutes=minutes)).isoformat()

    def journey(*legs: JourneyLeg) -> Journey:
        return Journey(
            legs=list(legs),
            depart=legs[0].depart_scheduled,
            arrive=legs[-1].arrive_scheduled,
            duration_minutes=0,
            changes=1,
        )

    ok = journey(_leg(iso(0), iso(49), "On time", "On time"), _leg(iso(58), iso(83)))
    late = journey(_leg(iso(0), iso(49), "13:04", "13:55"), _leg(iso(58), iso(83), "On time"))
    unknown = journey(_leg(iso(0), iso(49), "Delayed"), _leg(iso(58), iso(83)))
    assert server._at_risk(ok, 5) is False
    assert server._at_risk(late, 5) is True
    assert server._at_risk(unknown, 5) is True


def test_when_parsing() -> None:
    assert server._when("2026-10-02", "09:30").isoformat() == "2026-10-02T09:30:00+01:00"
    assert server._when("2026-12-02", "09:30").isoformat() == "2026-12-02T09:30:00+00:00"
    assert server._when("tomorrow", None).date() == datetime.now(UK_TZ).date() + timedelta(days=1)


@respx.mock
async def test_darwin_outage_falls_back_to_booked_times(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with connect(tmp_path, monkeypatch) as client:
        respx.get(f"{DARWIN_DEPARTURES_URL}/GetDepBoardWithDetails/MKT").mock(
            return_value=httpx.Response(200, text="<html>maintenance</html>")
        )
        out = await call(client, "live_departures", station="Marks Tey")
    assert out["source"] == "timetable"
    assert "unavailable" in out["messages"][0]


async def test_same_station_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async with connect(tmp_path, monkeypatch) as client:
        msg = await error(client, "plan_journey", origin="LST", destination="Liverpool Street")
    assert "both" in msg


async def test_future_timetable_without_time_covers_whole_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with connect(tmp_path, monkeypatch) as client:
        out = await call(
            client, "timetable", station="Marks Tey", date="2026-10-02", to="SUY", rows=100
        )
    times = [s["scheduled"] for s in out["services"]]
    assert times[0] == "06:58" and times[-1] == "22:58"


def test_risk_carries_through_tube_link() -> None:
    base = datetime(2026, 10, 2, 11, 0, tzinfo=UK_TZ)

    def iso(minutes: int) -> str:
        return (base + timedelta(minutes=minutes)).isoformat()

    tube = JourneyLeg(
        service_id="",
        mode="tube (approx.)",
        board_at=StationRef(name="KGX", crs="KGX"),
        alight_at=StationRef(name="LST", crs="LST"),
        destination="LST",
        depart_scheduled=iso(50),
        arrive_scheduled=iso(74),
    )
    late_in = _leg(iso(0), iso(50), "11:40", "12:30")  # 40 minutes late into KGX
    onward = _leg(iso(80), iso(129))  # 12:20 from LST
    j = Journey(
        legs=[late_in, tube, onward],
        depart=iso(0),
        arrive=iso(129),
        duration_minutes=129,
        changes=1,
    )
    assert server._at_risk(j, 5) is True
    on_time = Journey(
        legs=[_leg(iso(0), iso(50), "On time"), tube, onward],
        depart=iso(0),
        arrive=iso(129),
        duration_minutes=129,
        changes=1,
    )
    assert server._at_risk(on_time, 5) is False


def test_live_time_before_midnight() -> None:
    t = server._live_time("2026-10-03T00:05:00+01:00", "23:59")
    assert t is not None and t.isoformat() == "2026-10-02T23:59:00+01:00"
