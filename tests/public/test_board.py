"""The shareable departure boards: /board/LST, /board/LST/9, /board/LST/COL and ?embed=1."""

from __future__ import annotations

import asyncio
import html
import re
from datetime import datetime
from pathlib import Path

import pytest
from django.test import Client

from apps.public import board as boards
from apps.public import boards as views
from apps.rail import runtime
from apps.rail.config import UK_TZ
from apps.rail.schemas import Board
from tests.public.fakes import BASE, FakeFetch

pytestmark = pytest.mark.django_db

TALLY = '<script async data-domain="traintrackr.live" src="https://fiveb.ar/js/tally.js"></script>'
# The views' own board store and loop, before the fixtures put fakes in their place.
REAL_SHARED = views.shared
REAL_RUN = runtime.run


def _flat(text: str) -> str:
    """``text`` with each run of whitespace as one space, however the template wraps it."""
    return " ".join(text.split())


def _text(response: object) -> str:
    """A response's body as text, flattened (see ``_flat``)."""
    return _flat(response.content.decode())  # type: ignore[attr-defined]


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
def test_each_board_has_one_address(
    client: Client, fetch: FakeFetch, path: str, target: str
) -> None:
    """However a board is asked for, it is shown at its own address.

    So every share and embed of it is the same link.
    """
    r = client.get(path)
    assert r.status_code == 308
    assert r["Location"] == target
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
def test_an_address_that_is_not_a_board_goes_to_the_picker(
    client: Client, path: str, query: str
) -> None:
    """The picker says why the address isn't a board, or asks which station."""
    r = client.get(path)
    assert r.status_code == 303
    assert r["Location"].replace("-", "+") == f"/board?{query}"


# ---------------------------------------------------------------- the picker


def test_the_picker_is_a_form_that_works_without_javascript(client: Client) -> None:
    """A plain GET form; site.js only adds suggestions as someone types."""
    r = client.get("/board")
    assert r.status_code == 200
    text = _text(r)
    assert '<form class="picker" method="get" action="/board">' in text
    assert 'name="station"' in text
    assert 'name="platform"' in text
    assert 'name="to"' in text
    assert 'data-suggest="/api/stations"' in text
    assert '<a href="/board/LST">London Liverpool Street departures</a>' in text
    assert '<a href="/board" aria-current="page">Boards</a>' in text  # in the footer
    # The form sends to this site.
    assert "form-action 'self'" in r["Content-Security-Policy"]


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
def test_the_picker_goes_to_the_board(client: Client, query: dict[str, str], target: str) -> None:
    """A station the form names is a 303 to its board; #new tells site.js to count it."""
    r = client.get("/board", query)
    assert r.status_code == 303
    assert r["Location"] == target + "#new"


def test_the_picker_asks_which_station(client: Client) -> None:
    """A name that fits several stations lists them, keeping what else was typed."""
    r = client.get("/board", {"station": "sudbury", "platform": "2"})
    assert r.status_code == 200
    text = _text(r)
    assert "Which station?" in text
    for crs in ("SUY", "SDH", "SUD"):
        assert f'href="/board?station={crs}&amp;platform=2"' in text
    assert 'value="sudbury"' in text
    assert 'value="2"' in text


def test_the_picker_says_what_is_wrong(client: Client) -> None:
    """No such station is a 404 and a bad platform a 400, each saying so, escaped."""
    r = client.get("/board", {"station": "zzqqxxjj"})
    assert r.status_code == 404
    assert "No station matches &#x27;zzqqxxjj&#x27;" in r.content.decode()
    r = client.get("/board", {"station": "LST", "platform": "<b>"})
    assert r.status_code == 400
    body = r.content.decode()
    assert "&#x27;&lt;b&gt;&#x27; isn&#x27;t a platform" in body
    assert "<b>" not in body.split("<main", 1)[1]


def test_a_board_is_for_a_destination_or_a_platform_not_both(client: Client) -> None:
    """The picker refuses both, and no address has both."""
    r = client.get("/board", {"station": "LST", "to": "COL", "platform": "9"})
    assert r.status_code == 400
    assert "A board is for a destination or a platform, not both." in r.content.decode()
    assert client.get("/api/board/LST/COL/9").status_code == 404


def test_the_picker_asks_which_destination(client: Client) -> None:
    """Each choice goes back to the picker with that station filled in, and the rest kept."""
    r = client.get("/board", {"station": "Cambridge", "to": "sudbury"})
    assert r.status_code == 200
    text = _text(r)
    assert "Going to which station?" in text
    assert 'href="/board?station=Cambridge&amp;to=SUY"' in text


def test_station_suggestions(client: Client) -> None:
    """A few stations for what has been typed; nothing for one letter or a flood."""
    r = client.get("/api/stations", {"q": "sudbury"})
    assert r.status_code == 200
    crs = [s["crs"] for s in r.json()]
    assert {"SUY", "SDH", "SUD"} <= set(crs)
    assert len(crs) <= boards.MAX_SUGGESTIONS
    assert client.get("/api/stations", {"q": "s"}).json() == []
    assert client.get("/api/stations", {"q": "x" * 500}).json() == []


# ---------------------------------------------------------------- the board


def test_a_board_is_a_page_with_the_trains_in_it(client: Client, fetch: FakeFetch) -> None:
    """It reads without JavaScript: the trains are a table; site.js draws the flaps."""
    r = client.get("/board/LST")
    assert r.status_code == 200
    text = _text(r)
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
    assert '<span class="updated">Updated ' in text
    assert '<span class="next"></span>' in text
    # The platforms in use, each a board of its own.
    assert '<a href="/board/LST" aria-current="page">All</a>' in text
    assert '<a href="/board/LST/9">9</a>' in text
    assert fetch.calls == [("LST", None, None)]


def test_a_board_for_one_destination(client: Client, fetch: FakeFetch) -> None:
    """Only the trains calling there, with a way back to every train."""
    r = client.get("/board/LST/NRW")
    assert r.status_code == 200
    text = _text(r)
    assert '<figure class="departures live" data-feed="/api/board/LST/NRW"' in text
    assert "<span>Departures calling at Norwich</span>" in text
    assert "<title>London Liverpool Street departures to Norwich · Traintrackr</title>" in text
    assert f'<code id="board-link">{BASE}/board/LST/NRW</code>' in text
    assert '<a href="/board/LST">Every train from London Liverpool Street</a>' in text
    # A board is for a destination or a platform: no platform boards from here.
    assert "One platform" not in text
    assert fetch.calls == [("LST", "NRW", None)]
    data = client.get("/api/board/LST/NRW").json()
    assert data["to"] == {"name": "Norwich", "crs": "NRW"}
    assert data["platform"] is None
    assert [t["place"] for t in data["trains"]] == ["Norwich & Clacton-on-Sea"]
    empty = _text(client.get("/board/LST/COL"))
    assert (
        '<p class="empty" data-empty="No departures calling at Colchester in the next two hours.">'
        "No departures calling at Colchester in the next two hours.</p>"
    ) in empty


def test_a_board_can_be_shared_and_embedded(client: Client) -> None:
    """Its link and embed code, ready to copy, and a link preview that names it."""
    r = client.get("/board/LST/9")
    body = r.content.decode()
    text = _flat(body)
    assert f'<code id="board-link">{BASE}/board/LST/9</code>' in text
    frame = html.unescape(re.search(r'<code id="embed-code">(.*?)</code>', text).group(1))  # type: ignore[union-attr]
    assert frame.startswith(f'<iframe src="{BASE}/board/LST/9?embed=1" ')
    assert 'title="London Liverpool Street platform 9 departures"' in frame
    assert 'data-copy="#board-link"' in text
    assert 'data-copy="#embed-code"' in text
    # The link preview names the board. No og:url: the chat already has the link.
    assert (
        '<meta property="og:title" content="London Liverpool Street platform 9 departures' in body
    )
    assert "og:url" not in body
    assert "<title>London Liverpool Street platform 9 departures · Traintrackr</title>" in body
    policy = r["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in policy
    assert "connect-src 'self' https://fiveb.ar;" in policy
    assert "unsafe-inline" not in policy
    assert "script-src 'self' https://fiveb.ar;" in policy
    assert TALLY in body


def test_an_embedded_board_is_the_board_alone_and_may_be_framed(client: Client) -> None:
    """Framed in another page, it credits the data and links back in a tab of its own."""
    r = client.get("/board/LST/9", {"embed": "1"})
    assert r.status_code == 200
    assert "frame-ancestors *" in r["Content-Security-Policy"]
    assert "connect-src 'self' https://fiveb.ar;" in r["Content-Security-Policy"]
    body = r.content.decode()
    assert TALLY in body
    text = _flat(body)
    assert '<html lang="en-GB" class="embed">' in text
    assert "<nav" not in text
    assert "Share this board" not in text
    assert f'<link rel="canonical" href="{BASE}/board/LST/9" />' in text
    assert "Powered by National Rail Enquiries" in text
    # A path on the site: inside the frame it resolves against the board's own address.
    assert '<a href="/board/LST/9" target="_blank" rel="noopener">Traintrackr</a>' in text


def test_a_board_on_a_tv_is_the_board_alone(client: Client) -> None:
    """The board fills the screen: site.js sizes it and keeps the screen awake."""
    r = client.get("/board/LST/COL", {"tv": "1"})
    assert r.status_code == 200
    body = r.content.decode()
    text = _flat(body)
    assert '<html lang="en-GB" class="tv">' in text
    assert "<nav" not in text
    assert "Share this board" not in text
    assert '<figure class="departures live" data-feed="/api/board/LST/COL"' in text
    assert 'data-station="LST" data-by="destination"' in text
    assert TALLY in body
    assert '<p class="stale-note" hidden></p>' in text
    assert '<button type="button" class="fullscreen quiet" hidden>Full screen</button>' in text
    assert f'<link rel="canonical" href="{BASE}/board/LST/COL" />' in text
    # Unlike an embed, it is not for other sites to frame.
    assert "frame-ancestors 'none'" in r["Content-Security-Policy"]
    # A loose address keeps ?tv=1 on its way to the board's own.
    r = client.get("/board/lst/colchester", {"tv": "1"})
    assert r.status_code == 308
    assert r["Location"] == "/board/LST/COL?tv=1"


def test_the_board_page_offers_the_tv_view(client: Client) -> None:
    """The button needs JavaScript, so it starts hidden; the link works without."""
    text = _text(client.get("/board/LST"))
    assert '<h2 id="tv">Show it on a TV</h2>' in text
    assert '<button type="button" data-tv hidden>Show on TV</button>' in text
    assert f'<code id="tv-link">{BASE}/board/LST?tv=1</code>' in text


def test_a_platform_with_no_trains_says_so(client: Client) -> None:
    """An empty platform is an empty board that says which platform."""
    assert client.get("/api/board/LST/4").json()["trains"] == []
    page = _text(client.get("/board/LST/4"))
    assert "No departures from platform 4 in the next two hours.</p>" in page


def test_the_feed_is_the_board_as_data(client: Client) -> None:
    """What site.js fetches each minute, kept by no cache past the minute."""
    r = client.get("/api/board/LST/9")
    assert r.status_code == 200
    data = r.json()
    assert data["station"] == {"name": "London Liverpool Street", "crs": "LST"}
    assert data["platform"] == "9"
    assert data["platforms"] == ["9", "14"]
    assert data["source"] == "darwin"
    assert re.fullmatch(r"\d\d:\d\d:\d\d", data["updated"])
    assert data["trains"] == [
        {
            "time": "15:40",
            "place": "Norwich & Clacton-on-Sea",
            "platform": "9",
            "expected": "15:44",
        }
    ]
    # The pages fetch again as the minute turns.
    age = re.fullmatch(r"public, max-age=(\d+)", r["Cache-Control"])
    assert age
    assert int(age[1]) < 60
    for path in ("/api/board/XYZ", "/api/board/LST/nine!"):
        assert client.get(path).status_code == 404


async def test_every_viewer_at_once_shares_one_fetch() -> None:
    """A board embedded in a busy page is opened by all its visitors at once; Darwin is asked once.

    The views' sequential sharing is test_public.test_every_viewer_shares_one_fetch.
    """
    fetch = FakeFetch()
    shared = boards.SharedBoards(fetch)
    key = boards.BoardKey("LST")
    answers = await asyncio.gather(
        *(shared.get_for_minute(key) for _ in range(8)), *(shared.get(key) for _ in range(4))
    )
    assert len(answers) == 12
    assert fetch.calls == [("LST", None, None)]
    await shared.get(boards.BoardKey("LST", platform="9"))
    assert fetch.calls == [("LST", None, None), ("LST", None, "9")]


async def test_a_board_is_fetched_again_once_it_is_old() -> None:
    """Past its ``ttl`` a board is fetched again."""
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
        (datetime(2026, 9, 30, 23, 22, 45, tzinfo=UK_TZ), "23:22:45", 15.0),
        # A page whose clock is a little ahead asks as it turns to 23:23:00: it
        # gets that minute's board, stamped with its first second.
        (datetime(2026, 9, 30, 23, 22, 59, 500000, tzinfo=UK_TZ), "23:23:00", 60.5),
    ],
)
async def test_a_board_is_kept_for_its_minute(
    monkeypatch: pytest.MonkeyPatch, asked: datetime, updated: str, ttl: float
) -> None:
    """A board is kept until its minute is up, and the rest of that minute shares it."""
    kept: list[float] = []
    fetch = FakeFetch()
    shared = boards.SharedBoards(fetch, ttl=61.0)
    real_set = shared._kept.set

    def spy(key: object, value: object, seconds: float) -> None:
        """Note how long each board is kept for, and keep it."""
        kept.append(seconds)
        real_set(key, value, seconds)

    monkeypatch.setattr(shared._kept, "set", spy)
    asked_at = asked.timestamp()
    monkeypatch.setattr("apps.public.board.time.time", lambda: asked_at)
    data = await shared.get(boards.BoardKey("LST"))
    assert data["updated"] == updated
    assert kept == [pytest.approx(ttl)]
    await shared.get(boards.BoardKey("LST"))
    assert len(fetch.calls) == 1


async def test_a_board_fetched_across_the_minute_is_not_cached_into_the_next(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Asked for at 23:22:50, a fetch that ends at 23:23:05 is for 23:22, and over."""
    clock = [datetime(2026, 9, 30, 23, 22, 50, tzinfo=UK_TZ).timestamp()]
    monkeypatch.setattr("apps.public.board.time.time", lambda: clock[0])
    fetch = FakeFetch()

    async def slow(crs: str, to: str | None, platform: str | None) -> tuple[Board, list[str]]:
        """The fake fetch, taking 15 seconds."""
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
    """Expected says how late a train is, as the flaps do; early or on time, as it is."""
    assert boards.said(time, expected) == says


def test_a_failure_is_shared_too(client: Client, fetch: FakeFetch) -> None:
    """A board that can't be had is asked for once; the feed and the page say so."""
    fetch.error = "National Rail live data (Darwin) is offline."
    r = client.get("/api/board/LST")
    assert r.status_code == 503
    assert r.json() == {"error": boards.FEED_UNAVAILABLE}  # the page says why
    page = client.get("/board/LST")
    assert page.status_code == 503
    assert "National Rail live data (Darwin) is offline." in page.content.decode()
    assert len(fetch.calls) == 1


@pytest.mark.parametrize("mode", ["tv", "embed"])
def test_a_screen_nobody_reads_shows_why_and_recovers(
    client: Client, fetch: FakeFetch, mode: str
) -> None:
    """A TV that reloads during an outage gets the board, with the reason in it.

    Nobody is there to read the picker's form; site.js puts the usual message
    back once a fetch works.
    """
    fetch.error = "National Rail live data (Darwin) is offline."
    r = client.get("/board/LST/9", {mode: "1"})
    assert r.status_code == 503
    text = _text(r)
    assert '<figure class="departures live" data-feed="/api/board/LST/9"' in text
    assert (
        '<p class="empty" data-empty="No departures from platform 9 in the next two hours.">'
        "National Rail live data (Darwin) is offline.</p>"
    ) in text
    assert '<form class="picker"' not in text


def test_one_address_can_open_boards_only_so_fast(client: Client) -> None:
    """Past the burst the feed and the pages are refused, for that address only."""
    burst = int(boards.BOARD_LIMIT[0])
    here = "203.0.113.9"
    for n in range(burst):
        assert client.get(f"/api/board/LST/{n % 9 + 1}", REMOTE_ADDR=here).status_code == 200
    refused = client.get("/api/board/LST", REMOTE_ADDR=here)
    assert refused.status_code == 429
    assert int(refused["Retry-After"]) >= 1
    page = client.get("/board/LST", REMOTE_ADDR=here)
    assert page.status_code == 429
    # A refusal is for this address only: no shared cache may hand it to others.
    for r in (refused, page):
        assert r["Cache-Control"] == "private, no-store"
    assert client.get("/api/board/LST", REMOTE_ADDR="198.51.100.7").status_code == 200


# ------------------------------------------------------------ the whole app


def test_boards_in_the_app(client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Through the views as deployed, on the train tools' loop, with demo mode's trains."""
    monkeypatch.setenv("TRAINTRACKER_DEMO", "1")
    monkeypatch.setenv("TRAINTRACKER_DATA_DIR", str(tmp_path))
    REAL_SHARED.cache_clear()
    monkeypatch.setattr(views, "shared", REAL_SHARED)
    monkeypatch.setattr(runtime, "run", REAL_RUN)
    try:
        data = client.get("/api/board/CBG").json()
        assert data["station"]["crs"] == "CBG"
        assert data["source"] == "darwin"
        page = client.get("/board/CBG")
        assert page.status_code == 200
        assert "<h1>Cambridge</h1>" in page.content.decode()
        # The other pages link to the boards.
        assert '<a href="/board">Boards</a>' in client.get("/").content.decode()
    finally:
        runtime.stop()
        REAL_SHARED.cache_clear()
