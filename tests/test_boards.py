"""The shareable departure boards: /board/LST, /board/LST/9, /board/LST/COL and ?embed=1."""

from __future__ import annotations

import asyncio
import html
import os
import re
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError
from starlette.applications import Starlette

from traintracker import server, stations
from traintracker.config import UK_TZ, Settings
from traintracker.http_app import build_app
from traintracker.models import Board, BoardService, StationRef
from traintracker.site import boards

BASE = "https://tt.test"
TALLY = '<script async data-domain="traintrackr.live" src="https://fiveb.ar/js/tally.js"></script>'
FIELDS = {"site_url": BASE, "mcp_url": f"{BASE}/mcp", "fair_use": "Be kind."}


def _board(crs: str = "LST", name: str = "London Liverpool Street") -> Board:
    def service(time: str, to: list[str], platform: str | None, **kw: object) -> BoardService:
        return BoardService(
            service_id=f"darwin:{time}",
            source="darwin",
            origin=[StationRef(name=name, crs=crs)],
            destination=[StationRef(name=d) for d in to],
            scheduled=time,
            platform=platform,
            **kw,  # type: ignore[arg-type]
        )

    return Board(
        station=StationRef(name=name, crs=crs),
        board="departures",
        date="2026-10-02",
        source="darwin",
        services=[
            service("15:36", ["Colchester Town"], "14", expected="On time", status="on_time"),
            service("15:40", ["Norwich", "Clacton-on-Sea"], "9", expected="15:44*"),
            service("15:43", ["Cheshunt"], None, expected="15:43", status="cancelled"),
        ],
        messages=["Lifts are out of order at <this> station."],
    )


class FakeFetch:
    def __init__(self, error: str | None = None) -> None:
        self.calls: list[tuple[str, str | None, str | None]] = []
        self.error = error

    async def __call__(
        self, crs: str, to: str | None, platform: str | None
    ) -> tuple[Board, list[str]]:
        self.calls.append((crs, to, platform))
        await asyncio.sleep(0.01)  # long enough for other requests to arrive meanwhile
        if self.error:
            raise ToolError(self.error)
        board = _board()
        if platform:
            board.services = [s for s in board.services if s.platform == platform]
        if to:
            # Near enough for these trains: those that end there call there.
            name = stations.by_crs(to).name  # type: ignore[union-attr]
            board.services = [s for s in board.services if s.destination[0].name == name]
        return board, ["9", "14"]


@pytest.fixture
def fetch() -> FakeFetch:
    return FakeFetch()


@pytest.fixture
async def client(fetch: FakeFetch) -> AsyncIterator[httpx.AsyncClient]:
    app = Starlette(routes=boards.routes(FIELDS, fetch, "x-forwarded-for"))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as c:
        yield c


def _flat(text: str) -> str:
    return " ".join(text.split())


# ---------------------------------------------------------------- addresses


@pytest.mark.parametrize(
    ("path", "target"),
    [
        ("/board/lst", "/board/LST"),
        ("/board/LST/p9", "/board/LST/9"),
        ("/board/LST/Platform-9", "/board/LST/9"),
        ("/board/cambridge/1?embed=1", "/board/CBG/1?embed=1"),
        ("/board/london-liverpool-street", "/board/LST"),
        ("/board/kings%20cross/10a", "/board/KGX/10A"),
        ("/board/lst/col", "/board/LST/COL"),
        ("/board/LST/colchester?embed=1", "/board/LST/COL?embed=1"),
        ("/board/cambridge/Kings-Cross", "/board/CBG/KGX"),
        ("/board/LST/LST", "/board/LST"),
    ],
)
async def test_each_board_has_one_address(
    client: httpx.AsyncClient, fetch: FakeFetch, path: str, target: str
) -> None:
    # However a board is asked for, it is shown at its own address, so every
    # share and embed of it is the same link.
    r = await client.get(path)
    assert r.status_code == 308
    assert r.headers["location"] == target
    assert not fetch.calls


@pytest.mark.parametrize(
    ("path", "query"),
    [
        ("/board/sudbury", "station=sudbury"),
        ("/board/nowhere-at-all-xyz", "station=nowhere+at+all+xyz"),
        ("/board/LST/nine!", "station=LST&to=nine%21"),
        ("/board/LST/12!", "station=LST&platform=12%21"),
        ("/board/LST/nowhere-at-all-xyz", "station=LST&to=nowhere+at+all+xyz"),
    ],
)
async def test_an_address_that_is_not_a_board_goes_to_the_picker(
    client: httpx.AsyncClient, path: str, query: str
) -> None:
    r = await client.get(path)
    assert r.status_code == 303
    assert r.headers["location"].replace("-", "+") == f"/board?{query}"


# ---------------------------------------------------------------- the picker


async def test_the_picker_is_a_form_that_works_without_javascript(
    client: httpx.AsyncClient,
) -> None:
    r = await client.get("/board")
    assert r.status_code == 200
    text = _flat(r.text)
    assert '<form class="picker" method="get" action="/board">' in text
    assert 'name="station"' in text and 'name="platform"' in text and 'name="to"' in text
    assert 'data-suggest="/api/stations"' in text
    assert '<a href="/board/LST">London Liverpool Street departures</a>' in text
    assert '<a href="/board" aria-current="page">Boards</a>' in text
    # The form sends to this site, which the other pages' policy refuses.
    assert "form-action 'self'" in r.headers["content-security-policy"]


@pytest.mark.parametrize(
    ("query", "target"),
    [
        ({"station": "Cambridge", "platform": "1"}, "/board/CBG/1"),
        ({"station": "London Liverpool Street (LST)", "platform": ""}, "/board/LST"),
        ({"station": "kgx", "platform": "Platform 10a"}, "/board/KGX/10A"),
        ({"station": "LST", "to": "Colchester", "platform": ""}, "/board/LST/COL"),
        ({"station": "LST", "to": "LST"}, "/board/LST"),
    ],
)
async def test_the_picker_goes_to_the_board(
    client: httpx.AsyncClient, query: dict[str, str], target: str
) -> None:
    r = await client.get("/board", params=query)
    assert r.status_code == 303
    # #new tells site.js to count a new board.
    assert r.headers["location"] == target + "#new"


async def test_the_picker_asks_which_station(client: httpx.AsyncClient) -> None:
    r = await client.get("/board", params={"station": "sudbury", "platform": "2"})
    assert r.status_code == 200
    text = _flat(r.text)
    assert "Which station?" in text
    for crs in ("SUY", "SDH", "SUD"):
        assert f'href="/board?station={crs}&amp;platform=2"' in text
    assert 'value="sudbury"' in text and 'value="2"' in text


async def test_the_picker_says_what_is_wrong(client: httpx.AsyncClient) -> None:
    r = await client.get("/board", params={"station": "zzqqxxjj"})
    assert r.status_code == 404
    assert "No station matches &#x27;zzqqxxjj&#x27;" in r.text
    r = await client.get("/board", params={"station": "LST", "platform": "<b>"})
    assert r.status_code == 400
    assert "&#x27;&lt;b&gt;&#x27; isn&#x27;t a platform" in r.text
    assert "<b>" not in r.text.split("<main", 1)[1]


async def test_a_board_is_for_a_destination_or_a_platform_not_both(
    client: httpx.AsyncClient,
) -> None:
    r = await client.get("/board", params={"station": "LST", "to": "COL", "platform": "9"})
    assert r.status_code == 400
    assert "A board is for a destination or a platform, not both." in r.text
    assert (await client.get("/api/board/LST/COL/9")).status_code == 404


async def test_the_picker_asks_which_destination(client: httpx.AsyncClient) -> None:
    r = await client.get("/board", params={"station": "Cambridge", "to": "sudbury"})
    assert r.status_code == 200
    text = _flat(r.text)
    assert "Going to which station?" in text
    # Each choice goes back to the picker with that station filled in, and the rest kept.
    assert 'href="/board?station=Cambridge&amp;to=SUY"' in text


async def test_station_suggestions(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/stations", params={"q": "sudbury"})
    assert r.status_code == 200
    crs = [s["crs"] for s in r.json()]
    assert {"SUY", "SDH", "SUD"} <= set(crs) and len(crs) <= boards.MAX_SUGGESTIONS
    assert (await client.get("/api/stations", params={"q": "s"})).json() == []
    assert (await client.get("/api/stations", params={"q": "x" * 500})).json() == []


# ---------------------------------------------------------------- the board


async def test_a_board_is_a_page_with_the_trains_in_it(
    client: httpx.AsyncClient, fetch: FakeFetch
) -> None:
    r = await client.get("/board/LST")
    assert r.status_code == 200
    text = _flat(r.text)
    # It reads without JavaScript: the trains are a table; site.js draws the flaps.
    assert (
        '<figure class="departures live" data-feed="/api/board/LST" data-refresh="60" '
        'data-station="LST" data-by="all">'
    ) in text
    assert "<h1>London Liverpool Street</h1>" in text
    assert '<td data-label="Destination">Colchester Town</td>' in text
    # As the chat's board shows them: every destination, no forecast mark, and
    # a cancelled train says so.
    assert '<td data-label="Destination">Norwich &amp; Clacton-on-Sea</td>' in text
    assert '<td data-label="Expected" data-expected="15:44">15:44, 4 minutes late</td>' in text
    assert '<td data-label="Expected" data-expected="Cancelled">Cancelled</td>' in text
    assert "<li>Lifts are out of order at &lt;this&gt; station.</li>" in text
    assert "Powered by National Rail Enquiries" in text
    assert re.search(r'<time class="clock" datetime="[^"]+">\d\d:\d\d:\d\d</time>', text)
    # site.js counts down to the next update here.
    assert '<span class="updated">Updated ' in text and '<span class="next"></span>' in text
    # The platforms in use, each a board of its own.
    assert '<a href="/board/LST" aria-current="page">All</a>' in text
    assert '<a href="/board/LST/9">9</a>' in text
    assert fetch.calls == [("LST", None, None)]


async def test_a_board_for_one_destination(client: httpx.AsyncClient, fetch: FakeFetch) -> None:
    r = await client.get("/board/LST/NRW")
    assert r.status_code == 200
    text = _flat(r.text)
    assert '<figure class="departures live" data-feed="/api/board/LST/NRW"' in text
    assert "<span>Departures calling at Norwich</span>" in text
    assert "<title>London Liverpool Street departures to Norwich · Traintrackr</title>" in text
    assert f'<code id="board-link">{BASE}/board/LST/NRW</code>' in text
    assert '<a href="/board/LST">Every train from London Liverpool Street</a>' in text
    # A board is for a destination or a platform: no platform boards from here.
    assert "One platform" not in text
    assert fetch.calls == [("LST", "NRW", None)]
    data = (await client.get("/api/board/LST/NRW")).json()
    assert data["to"] == {"name": "Norwich", "crs": "NRW"} and data["platform"] is None
    assert [t["place"] for t in data["trains"]] == ["Norwich & Clacton-on-Sea"]
    empty = _flat((await client.get("/board/LST/COL")).text)
    assert (
        '<p class="empty" data-empty="No departures calling at Colchester in the next two hours.">'
        "No departures calling at Colchester in the next two hours.</p>"
    ) in empty


async def test_a_board_can_be_shared_and_embedded(client: httpx.AsyncClient) -> None:
    r = await client.get("/board/LST/9")
    text = _flat(r.text)
    assert f'<code id="board-link">{BASE}/board/LST/9</code>' in text
    frame = html.unescape(re.search(r'<code id="embed-code">(.*?)</code>', text).group(1))  # type: ignore[union-attr]
    assert frame.startswith(f'<iframe src="{BASE}/board/LST/9?embed=1" ')
    assert 'title="London Liverpool Street platform 9 departures"' in frame
    assert 'data-copy="#board-link"' in text and 'data-copy="#embed-code"' in text
    # The link preview names the board, at its own address.
    assert f'<meta property="og:url" content="{BASE}/board/LST/9" />' in r.text
    assert "<title>London Liverpool Street platform 9 departures · Traintrackr</title>" in r.text
    policy = r.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in policy
    assert "connect-src 'self' https://fiveb.ar;" in policy and "unsafe-inline" not in policy
    assert "script-src 'self' https://fiveb.ar;" in policy
    assert TALLY in r.text


async def test_an_embedded_board_is_the_board_alone_and_may_be_framed(
    client: httpx.AsyncClient,
) -> None:
    r = await client.get("/board/LST/9", params={"embed": "1"})
    assert r.status_code == 200
    assert "frame-ancestors *" in r.headers["content-security-policy"]
    assert "connect-src 'self' https://fiveb.ar;" in r.headers["content-security-policy"]
    assert TALLY in r.text
    text = _flat(r.text)
    assert '<html lang="en-GB" class="embed">' in text
    assert "<nav" not in text and "Share this board" not in text
    assert f'<link rel="canonical" href="{BASE}/board/LST/9" />' in text
    # Framed in another page, it credits the data and links back in a tab of its own.
    assert "Powered by National Rail Enquiries" in text
    assert f'<a href="{BASE}/board/LST/9" target="_blank" rel="noopener">Traintrackr</a>' in text


async def test_a_board_on_a_tv_is_the_board_alone(client: httpx.AsyncClient) -> None:
    r = await client.get("/board/LST/COL", params={"tv": "1"})
    assert r.status_code == 200
    text = _flat(r.text)
    # The board fills the screen: site.js sizes it and keeps the screen awake.
    assert '<html lang="en-GB" class="tv">' in text
    assert "<nav" not in text and "Share this board" not in text
    assert '<figure class="departures live" data-feed="/api/board/LST/COL"' in text
    assert 'data-station="LST" data-by="destination"' in text
    assert TALLY in r.text
    assert '<p class="stale-note" hidden></p>' in text
    assert '<button type="button" class="fullscreen quiet" hidden>Full screen</button>' in text
    assert f'<link rel="canonical" href="{BASE}/board/LST/COL" />' in text
    # Unlike an embed, it is not for other sites to frame.
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    # A loose address keeps ?tv=1 on its way to the board's own.
    r = await client.get("/board/lst/colchester", params={"tv": "1"})
    assert r.status_code == 308 and r.headers["location"] == "/board/LST/COL?tv=1"


async def test_the_board_page_offers_the_tv_view(client: httpx.AsyncClient) -> None:
    text = _flat((await client.get("/board/LST")).text)
    assert '<h2 id="tv">Show it on a TV</h2>' in text
    # The button needs JavaScript, so it starts hidden; the link works without.
    assert '<button type="button" data-tv hidden>Show on TV</button>' in text
    assert f'<code id="tv-link">{BASE}/board/LST?tv=1</code>' in text


async def test_a_platform_with_no_trains_says_so(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/board/LST/4")
    assert r.json()["trains"] == []
    page = _flat((await client.get("/board/LST/4")).text)
    assert "No departures from platform 4 in the next two hours.</p>" in page


async def test_the_feed_is_the_board_as_data(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/board/LST/9")
    assert r.status_code == 200
    data = r.json()
    assert data["station"] == {"name": "London Liverpool Street", "crs": "LST"}
    assert data["platform"] == "9" and data["platforms"] == ["9", "14"]
    assert data["source"] == "darwin" and re.fullmatch(r"\d\d:\d\d:\d\d", data["updated"])
    assert data["trains"] == [
        {
            "time": "15:40",
            "place": "Norwich & Clacton-on-Sea",
            "platform": "9",
            "expected": "15:44",
        }
    ]
    # Kept by no cache past the minute: the pages fetch again as it turns.
    age = re.fullmatch(r"public, max-age=(\d+)", r.headers["cache-control"])
    assert age and int(age[1]) < 60
    for path in ("/api/board/XYZ", "/api/board/LST/nine!"):
        assert (await client.get(path)).status_code == 404


async def test_every_viewer_shares_one_fetch(client: httpx.AsyncClient, fetch: FakeFetch) -> None:
    # A board embedded in a busy page is opened by all its visitors at once;
    # Darwin is asked once.
    answers = await asyncio.gather(
        *(client.get("/api/board/LST") for _ in range(8)),
        *(client.get("/board/LST") for _ in range(4)),
    )
    assert {r.status_code for r in answers} == {200}
    assert fetch.calls == [("LST", None, None)]
    await client.get("/api/board/LST/9")
    assert fetch.calls == [("LST", None, None), ("LST", None, "9")]


async def test_a_board_is_fetched_again_once_it_is_old() -> None:
    fetch = FakeFetch()
    shared = boards.SharedBoards(fetch, ttl=0.0)
    await shared.get(boards.BoardKey("LST"))
    await shared.get(boards.BoardKey("LST"))
    assert len(fetch.calls) == 2


@pytest.mark.parametrize(
    ("asked", "updated", "ttl"),
    [
        # Asked for at 23:22:45, a board is kept for the 15 seconds left of that
        # minute, not a whole minute: the pages ask again at 23:23:00.
        (datetime(2026, 9, 30, 23, 22, 45), "23:22:45", 15.0),
        # A page whose clock is a little ahead asks as it turns to 23:23:00: it
        # gets that minute's board, stamped with its first second.
        (datetime(2026, 9, 30, 23, 22, 59, 500000), "23:23:00", 60.5),
    ],
)
async def test_a_board_is_kept_for_its_minute(
    monkeypatch: pytest.MonkeyPatch, asked: datetime, updated: str, ttl: float
) -> None:
    kept: list[float] = []
    fetch = FakeFetch()
    shared = boards.SharedBoards(fetch, ttl=61.0)
    real_set = shared._kept.set

    def spy(key: object, value: object, seconds: float) -> None:
        kept.append(seconds)
        real_set(key, value, seconds)

    monkeypatch.setattr(shared._kept, "set", spy)
    monkeypatch.setattr(boards.time, "time", lambda: asked.replace(tzinfo=UK_TZ).timestamp())
    data = await shared.get(boards.BoardKey("LST"))
    assert data["updated"] == updated
    assert kept == [pytest.approx(ttl)]
    # The rest of that minute shares it.
    await shared.get(boards.BoardKey("LST"))
    assert len(fetch.calls) == 1


async def test_a_board_fetched_across_the_minute_is_not_cached_into_the_next(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Asked for at 23:22:50; the fetch ends at 23:23:05, in the next minute.
    clock = [datetime(2026, 9, 30, 23, 22, 50, tzinfo=UK_TZ).timestamp()]
    monkeypatch.setattr(boards.time, "time", lambda: clock[0])
    fetch = FakeFetch()

    async def slow(crs: str, to: str | None, platform: str | None) -> tuple[Board, list[str]]:
        clock[0] += 15
        return await fetch(crs, to, platform)

    minute, data = await boards.SharedBoards(slow).get_for_minute(boards.BoardKey("LST"))
    assert data["updated"] == "23:22:50"
    assert boards.minute_left(minute) == 0


@pytest.mark.parametrize(
    ("time", "expected", "says"),
    [
        ("15:40", "15:44", "15:44, 4 minutes late"),
        ("15:40", "15:41", "15:41, 1 minute late"),
        ("23:58", "00:03", "00:03, 5 minutes late"),
        ("15:40", "15:40", "15:40"),
        ("15:40", "15:38", "15:38"),
        ("15:40", "On time", "On time"),
        ("15:40", "Cancelled", "Cancelled"),
    ],
)
def test_the_table_says_how_late_a_train_is(time: str, expected: str, says: str) -> None:
    assert boards.said(time, expected) == says


async def test_a_failure_is_shared_too(client: httpx.AsyncClient, fetch: FakeFetch) -> None:
    fetch.error = "National Rail live data (Darwin) is offline."
    r = await client.get("/api/board/LST")
    assert r.status_code == 503
    assert r.json() == {"error": "National Rail live data (Darwin) is offline."}
    page = await client.get("/board/LST")
    assert page.status_code == 503
    assert "National Rail live data (Darwin) is offline." in page.text
    assert len(fetch.calls) == 1


@pytest.mark.parametrize("mode", ["tv", "embed"])
async def test_a_screen_nobody_reads_shows_why_and_recovers(
    client: httpx.AsyncClient, fetch: FakeFetch, mode: str
) -> None:
    # A TV that reloads during an outage has nobody to read the picker's
    # form: it gets the board, with the reason where the trains would be, and
    # site.js puts the usual message back once a fetch works.
    fetch.error = "National Rail live data (Darwin) is offline."
    r = await client.get("/board/LST/9", params={mode: "1"})
    assert r.status_code == 503
    text = _flat(r.text)
    assert '<figure class="departures live" data-feed="/api/board/LST/9"' in text
    assert (
        '<p class="empty" data-empty="No departures from platform 9 in the next two hours.">'
        "National Rail live data (Darwin) is offline.</p>"
    ) in text
    assert '<form class="picker"' not in text


async def test_one_address_can_open_boards_only_so_fast(
    client: httpx.AsyncClient, fetch: FakeFetch
) -> None:
    burst = int(boards.BOARD_LIMIT[0])
    here = {"x-forwarded-for": "203.0.113.9"}
    for n in range(burst):
        assert (await client.get(f"/api/board/LST/{n % 9 + 1}", headers=here)).status_code == 200
    refused = await client.get("/api/board/LST", headers=here)
    assert refused.status_code == 429 and int(refused.headers["retry-after"]) >= 1
    page = await client.get("/board/LST", headers=here)
    assert page.status_code == 429
    # A refusal is for this address only: no shared cache may hand it to others.
    for r in (refused, page):
        assert r.headers["cache-control"] == "private, no-store"
    # Another address is not held back.
    other = {"x-forwarded-for": "198.51.100.7"}
    assert (await client.get("/api/board/LST", headers=other)).status_code == 200


# ------------------------------------------------------------ the whole app


async def test_boards_in_the_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Through the app as deployed, with demo mode's trains.
    monkeypatch.setenv("TRAINTRACKER_DEMO", "1")
    monkeypatch.setenv("TRAINTRACKER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MCP_PUBLIC_URL", BASE)
    monkeypatch.setenv("MCP_PUBLIC_HOSTS", "tt.test")
    monkeypatch.setenv("MCP_OAUTH_PASSPHRASE", "correct horse battery staple")
    monkeypatch.setenv("MCP_AUTH_SCHEMA", os.environ["TIMETABLE_SCHEMA"] + "_auth")
    app = build_app(server.mcp, Settings.from_env())
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
            data = (await client.get("/api/board/CBG")).json()
            assert data["station"]["crs"] == "CBG"
            assert data["source"] == "darwin"
            page = await client.get("/board/CBG")
            assert page.status_code == 200
            assert "<h1>Cambridge</h1>" in page.text
            # The other pages link to the boards.
            home = (await client.get("/")).text
            assert '<a href="/board">Boards</a>' in home
