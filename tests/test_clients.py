"""Client parsing tests. Fixtures follow the published response shapes
(Darwin LDBWS JSON); they are not live recordings."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from traintracker.config import Settings
from traintracker.darwin import DarwinClient, status_from
from traintracker.errors import NotConfigured, RateLimited, UpstreamError

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Settings:
    for key in ("NR_USERNAME", "NR_PASSWORD", "DARWIN_ARRIVALS_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DARWIN_API_KEY", "darwin-key")
    monkeypatch.setenv("TRAINTRACKER_DATA_DIR", str(tmp_path))
    return Settings.from_env()


@pytest.mark.parametrize(
    ("scheduled", "expected", "cancelled", "result"),
    [
        ("13:00", "On time", False, ("on_time", 0)),
        ("13:00", "13:04", False, ("late", 4)),
        ("13:00", "12:59", False, ("early", -1)),
        ("23:58", "00:03", False, ("late", 5)),
        ("13:00", "Delayed", False, ("delayed", None)),
        ("13:00", "Cancelled", False, ("cancelled", None)),
        ("13:00", "On time", True, ("cancelled", None)),
        ("13:00", "No report", False, ("no_report", None)),
        ("13:00", "13:04*", False, ("late", 4)),
    ],
)
def test_darwin_status(
    scheduled: str, expected: str, cancelled: bool, result: tuple[str, int | None]
) -> None:
    assert status_from(scheduled, expected, cancelled) == result


@respx.mock
async def test_darwin_departures(settings: Settings) -> None:
    route = respx.get(f"{settings.darwin_departures_url}/GetDepBoardWithDetails/LST").mock(
        return_value=httpx.Response(200, json=load("darwin_departures_LST.json"))
    )
    async with httpx.AsyncClient() as http:
        board = await DarwinClient(settings, http).board("LST", "departures", filter_crs="MKT")

    req = route.calls.last.request
    assert req.headers["x-apikey"] == "darwin-key"
    assert req.url.params["filterCrs"] == "MKT"
    assert req.url.params["filterType"] == "to"

    assert board.station.crs == "LST"
    assert board.date == "2026-09-26"
    assert board.filter and board.filter.name == "Marks Tey"
    # Sorted by time with the replacement bus merged in.
    assert [s.scheduled for s in board.services] == ["12:30", "12:45", "13:00"]
    cancelled, bus, late = board.services
    assert cancelled.status == "cancelled" and "train crew" in (cancelled.reason or "")
    assert bus.mode == "bus"
    assert late.status == "late" and late.delay_minutes == 4
    assert late.service_id == "darwin:1234567LIVST___"
    assert late.calling_points and late.calling_points[1].station.crs == "MKT"
    assert board.messages == [
        "Engineering works between Shenfield and Colchester this weekend & next."
    ]


@respx.mock
async def test_darwin_errors(settings: Settings) -> None:
    url = f"{settings.darwin_departures_url}/GetDepBoardWithDetails/LST"
    async with httpx.AsyncClient() as http:
        client = DarwinClient(settings, http)
        respx.get(url).mock(return_value=httpx.Response(401))
        with pytest.raises(UpstreamError, match="credentials"):
            await client.board("LST", "departures")
        respx.get(url).mock(return_value=httpx.Response(429, headers={"Retry-After": "30"}))
        with pytest.raises(RateLimited, match="30s"):
            await client.board("LST", "departures", rows=5)


async def test_darwin_arrivals_need_config(settings: Settings) -> None:
    async with httpx.AsyncClient() as http:
        with pytest.raises(NotConfigured):
            await DarwinClient(settings, http).board("SUY", "arrivals")


def test_load_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from traintracker.config import load_dotenv

    env = tmp_path / ".env"
    env.write_text('# comment\nexport TT_A="quoted"\nTT_B=plain\nTT_C=from-file\nnot a pair\n')
    monkeypatch.delenv("TRAINTRACKER_NO_DOTENV")
    monkeypatch.delenv("TT_A", raising=False)
    monkeypatch.delenv("TT_B", raising=False)
    monkeypatch.setenv("TT_C", "from-env")
    load_dotenv(env)
    import os

    assert (os.environ["TT_A"], os.environ["TT_B"], os.environ["TT_C"]) == (
        "quoted",
        "plain",
        "from-env",
    )
    monkeypatch.delenv("TT_A")
    monkeypatch.delenv("TT_B")


@respx.mock
async def test_darwin_very_late_train_sorts_first(settings: Settings) -> None:
    data = load("darwin_departures_LST.json")
    data["generatedAt"] = "2026-09-26T12:00:00+01:00"
    data["trainServices"][1]["std"] = "11:20"  # still on the board, 40+ min late
    respx.get(f"{settings.darwin_departures_url}/GetDepBoardWithDetails/LST").mock(
        return_value=httpx.Response(200, json=data)
    )
    async with httpx.AsyncClient() as http:
        board = await DarwinClient(settings, http).board("LST", "departures", rows=2)
    assert [s.scheduled for s in board.services] == ["11:20", "12:45"]


@respx.mock
async def test_non_json_body_is_a_clear_error(settings: Settings) -> None:
    respx.get(f"{settings.darwin_departures_url}/GetDepBoardWithDetails/LST").mock(
        return_value=httpx.Response(200, text="<html>oops</html>")
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(UpstreamError, match="isn't JSON"):
            await DarwinClient(settings, http).board("LST", "departures")


def test_settings_repr_hides_secrets(settings: Settings) -> None:
    assert "darwin-key" not in repr(settings)


def _darwin_page(times: list[str]) -> dict[str, Any]:
    return {
        "generatedAt": "2026-09-26T12:55:00+01:00",
        "locationName": "London Liverpool Street",
        "crs": "LST",
        "trainServices": [
            {
                "std": t,
                "etd": "On time",
                "serviceID": f"S{t.replace(':', '')}",
                "origin": [{"locationName": "London Liverpool Street", "crs": "LST"}],
                "destination": [{"locationName": "Colchester", "crs": "COL"}],
            }
            for t in times
        ],
    }


@respx.mock
async def test_darwin_pages_past_ten_rows(settings: Settings) -> None:
    pages = {
        "0": [f"13:{m:02d}" for m in range(0, 10)],
        "14": [f"13:{m:02d}" for m in range(9, 19)],  # overlaps the first page by one
    }
    route = respx.get(f"{settings.darwin_departures_url}/GetDepBoardWithDetails/LST").mock(
        side_effect=lambda req: httpx.Response(
            200, json=_darwin_page(pages[req.url.params["timeOffset"]])
        )
    )
    async with httpx.AsyncClient() as http:
        board = await DarwinClient(settings, http).board("LST", "departures", rows=15)
    assert [s.scheduled for s in board.services] == [f"13:{m:02d}" for m in range(0, 15)]
    assert [c.request.url.params["numRows"] for c in route.calls] == ["10", "10"]


@respx.mock
async def test_darwin_keeps_first_page_when_a_later_page_fails(settings: Settings) -> None:
    def respond(req: httpx.Request) -> httpx.Response:
        if req.url.params["timeOffset"] == "0":
            return httpx.Response(200, json=_darwin_page([f"13:{m:02d}" for m in range(10)]))
        return httpx.Response(503)

    respx.get(f"{settings.darwin_departures_url}/GetDepBoardWithDetails/LST").mock(
        side_effect=respond
    )
    async with httpx.AsyncClient() as http:
        board = await DarwinClient(settings, http).board("LST", "departures", rows=20)
    assert len(board.services) == 10
