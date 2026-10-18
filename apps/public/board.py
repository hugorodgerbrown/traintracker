"""What a departure board is: its address, its data, and one fetch a minute for all.

A board is a station's live departures (/board/LST), one platform's
(/board/LST/9), or the trains calling at another station (/board/LST/COL).
Where no train goes to that station in the next two hours, the board shows
the journeys there with one change instead: each train with its connection
(/board/LST/SUY, changing at Marks Tey).
After the station, a station's code is a destination and anything else a
platform; a board is for one or the other, never both. ``parse`` reads an
address and ``BoardKey`` writes it.

Anyone can open a board without signing in, and a board embedded in a busy
page is opened by everyone who visits it. So what reaches Darwin is one fetch
per board a minute, not one per viewer: ``SharedBoards`` keeps each answer
until the minute is up (TTL at most) for every page showing that board, and
while it is being fetched other requests for it wait for the same answer. The
pages fetch on the minute, as their clocks turn to :00, so the board they get
was fetched then too, and says so ("Updated 23:23:00").

The views that draw the boards are in boards.py.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, cast
from urllib.parse import quote

from mcp.server.mcpserver.exceptions import ToolError

from apps.rail import stations
from apps.rail.config import UK_TZ
from apps.rail.errors import StationNotFound
from apps.rail.http import TTLCache
from apps.rail.schemas import Board, BoardService, Journey, JourneyLeg

logger = logging.getLogger(__name__)

PICKER_PATH = "/board"
FEED_PREFIX = "/api/board"
SUGGEST_PATH = "/api/stations"
NEW_BOARD = "#new"  # on the picker's redirect to a board: see site.js

TTL = 60.0  # seconds one fetched board is shared for at most: until the minute is up
EARLY = 2.0  # seconds before the minute a page's clock may turn to it
REFRESH = 60  # seconds between a page's fetches while it is visible
MAX_BOARDS = 2048  # boards kept at once; about one per station
MAX_SUGGESTIONS = 8
# What one client address may do: (at once, a minute). A page open on a screen
# takes one a minute; this leaves room for an office of screens behind one address.
BOARD_LIMIT = (30, 60.0)
# What Expected says for a train from the timetable, not the live feed.
SCHEDULED = "Scheduled"
# What the feed says when a board can't be had; the pages say why.
FEED_UNAVAILABLE = "Board temporarily unavailable."
# A platform as the board's address has it: "9", "10A", "B".
PLATFORM = re.compile(r"[0-9A-Z]{1,4}")
# "London Liverpool Street (LST)", as a suggestion fills the station field.
WITH_CODE = re.compile(r".*\(([A-Za-z]{3})\)\s*")
# The size the embed code gives the frame: ten trains in the wide layout.
EMBED_HEIGHT = 560
# The score (0-100) a station needs to be offered as "did you mean". Every
# query matches something a little.
CLOSE_ENOUGH = 70

Fetch = Callable[[str, str | None, str | None], Awaitable[tuple[Board, list[str]]]]
Changes = Callable[[str, str], Awaitable[list[Journey]]]


class BoardUnavailable(Exception):
    """The board couldn't be fetched. The message is safe to show."""


# ------------------------------------------------------------------ the data


@dataclass(frozen=True)
class BoardKey:
    """Which board: a station's departures, those calling at `to`, or one platform's.

    Each has one address.
    """

    crs: str
    to: str | None = None
    platform: str | None = None

    def __post_init__(self) -> None:
        if self.to and self.platform:
            raise ValueError("A board is for a destination or a platform, not both.")

    @property
    def tail(self) -> str:
        """The address after the station: the destination or the platform."""
        if self.to:
            return f"/{self.to}"
        return f"/{quote(self.platform)}" if self.platform else ""

    @property
    def path(self) -> str:
        """The board's page address: /board/LST/9."""
        return f"{PICKER_PATH}/{self.crs}{self.tail}"

    @property
    def feed(self) -> str:
        """The board's feed address: /api/board/LST/9."""
        return f"{FEED_PREFIX}/{self.crs}{self.tail}"

    def title(self) -> str:
        """The board's title: "London Liverpool Street platform 9 departures to Colchester"."""
        text = station_name(self.crs) + (f" platform {self.platform}" if self.platform else "")
        return text + " departures" + (f" to {station_name(self.to)}" if self.to else "")

    def empty(self) -> str:
        """What the board says when it has no trains."""
        if self.to:
            return (
                f"No trains to {station_name(self.to)}, direct or with one change, "
                "in the next two hours."
            )
        where = f" from platform {self.platform}" if self.platform else ""
        where += f" calling at {station_name(self.to)}" if self.to else ""
        return f"No departures{where} in the next two hours."

    @property
    def by(self) -> str:
        """Which trains the board picks: "all", "platform" or "destination"."""
        return "destination" if self.to else "platform" if self.platform else "all"

    def kind(self, changes: list[str] | None = None) -> str:
        """What the board shows under the station's name.

        For example "Platform 9 departures calling at Colchester", or, with the
        stations its journeys change at, "Departures to Sudbury (Suffolk),
        changing at Marks Tey".
        """
        if changes:
            where = f" at {changes[0]}" if len(set(changes)) == 1 else ""
            return f"Departures to {station_name(self.to)}, changing{where}"
        text = f"Platform {self.platform} departures" if self.platform else "Departures"
        return text + (f" calling at {station_name(self.to)}" if self.to else "")


EXAMPLES = (
    BoardKey("LST"),
    BoardKey("LST", "COL"),
    BoardKey("CBG", platform="1"),
    BoardKey("MAN"),
    BoardKey("EDB"),
)


def station_name(crs: str | None) -> str:
    """A station's name from its code, or the code itself if it isn't a station's."""
    station = stations.by_crs(crs or "")
    return station.name if station else crs or ""


def parse(station: str, then: str | None = None) -> BoardKey | None:
    """The board an address names, or None if it isn't exactly one.

    The address is a station's code, then optionally a destination's code or a
    platform. One that isn't exact may still name a board loosely (see `loose`).
    """
    st = stations.by_crs(station) if len(station) == 3 else None
    if st is None:
        return None
    if then is None:
        return BoardKey(st.crs)
    if len(then) == 3 and (to := stations.by_crs(then)):
        return BoardKey(st.crs, None if to.crs == st.crs else to.crs)
    platform = clean_platform(then)
    return BoardKey(st.crs, platform=platform) if platform else None


def _train(service: BoardService, board_date: str) -> dict[str, Any]:
    """What each column of the board says for one train, and which day it runs.

    The same as the chat's board app (ui/board.html) shows. A train from the
    timetable rather than the live feed says Scheduled in Expected, so it is
    never read as a live time (TRA-33). `day` names the day it runs on where
    that isn't the board's ("Monday 19 October"), and is empty otherwise.
    """
    if service.source == "timetable":
        expected = SCHEDULED
    elif service.status == "cancelled":
        expected = "Cancelled"
    else:
        expected = (service.expected or "").removesuffix("*")
    return {
        "time": service.scheduled or "",
        "place": " & ".join(p.name for p in service.destination),
        "platform": service.platform or "",
        "expected": expected,
        "scheduled": service.source == "timetable",
        "day": day_name(service.date) if service.date and service.date != board_date else "",
    }


def _hhmm(iso: str) -> str:
    """The time of an ISO datetime as a board shows it: "12:16"."""
    return datetime.fromisoformat(iso).strftime("%H:%M")


def _leg(leg: JourneyLeg) -> dict[str, Any]:
    """What each column of the board says for one train of a journey.

    A train with no live time says Scheduled, as a timetable train on the
    board does (TRA-33).
    """
    if leg.cancelled:
        expected = "Cancelled"
    elif leg.depart_expected:
        expected = leg.depart_expected.removesuffix("*")
    else:
        expected = SCHEDULED
    return {
        "time": _hhmm(leg.depart_scheduled),
        "place": leg.destination,
        "platform": leg.platform or "",
        "expected": expected,
        "scheduled": expected == SCHEDULED,
        "day": "",
    }


def _journey(journey: Journey) -> dict[str, Any]:
    """A journey with one change as the board shows it.

    The train to catch here, with its connection: where it is, and the
    connecting train's time, platform and expected.
    """
    first, then = journey.legs
    return _leg(first) | {"change": _leg(then) | {"at": then.board_at.name}}


def day_name(iso: str) -> str:
    """A date as a board's day divider says it: "Monday 19 October"."""
    day = date.fromisoformat(iso)
    return f"{day:%A} {day.day} {day:%B}"


def minute_left(minute: int) -> float:
    """Seconds until the clock turns from `minute` to the next, 0 once it has.

    UK minutes start when UTC's do.
    """
    return max(0.0, (minute + 1) * 60 - EARLY - time.time())


def payload(
    board: Board,
    platforms: list[str],
    key: BoardKey,
    updated: datetime,
    journeys: list[Journey] | None = None,
) -> dict[str, Any]:
    """A board as the page draws it, and as /api/board answers.

    `updated` is when it was asked for. With `journeys` (and no trains of its
    own), the board is those journeys, each a train and its connection.
    """
    trains: list[dict[str, Any]] = [_train(s, board.date) for s in board.services]
    changing = [] if trains else journeys or []
    return {
        "station": {"name": board.station.name, "crs": board.station.crs},
        "to": {"name": station_name(key.to), "crs": key.to} if key.to else None,
        "platform": key.platform,
        "platforms": platforms,
        "source": board.source,
        "updated": updated.strftime("%H:%M:%S"),
        "kind": key.kind([j.legs[1].board_at.name for j in changing]),
        "messages": board.messages[:3],
        "trains": trains or [_journey(j) for j in changing],
    }


@dataclass
class _Failed:
    message: str


class SharedBoards:
    """Fetched boards, each shared by every request for it in the same minute.

    Each is kept for `ttl` seconds at most.

    The pages fetch as their clocks turn to :00, and a clock can be a little
    ahead of ours: a request up to EARLY seconds before the minute counts as
    in it, and gets a board fetched for it and stamped with its first second.

    A failure is kept as long, so a board that can't be fetched isn't asked
    for again by every page showing it.
    """

    def __init__(self, fetch: Fetch, changes: Changes | None = None, ttl: float = TTL) -> None:
        self.fetch = fetch
        self.changes = changes
        self.ttl = ttl
        self._kept = TTLCache(MAX_BOARDS)
        self._pending: dict[
            tuple[BoardKey, int], asyncio.Future[tuple[int, dict[str, Any] | _Failed]]
        ] = {}

    async def get(self, key: BoardKey) -> dict[str, Any]:
        """The board for this minute, fetched once for everyone asking for it."""
        return (await self.get_for_minute(key))[1]

    async def get_for_minute(self, key: BoardKey) -> tuple[int, dict[str, Any]]:
        """The board, and the minute it is for."""
        now = time.time()
        minute = int((now + EARLY) // 60)
        kept = self._kept.get(key)
        if kept is not None and kept[0] == minute:
            found = kept[1]
        else:
            ask = (key, minute)
            if ask not in self._pending:
                self._pending[ask] = asyncio.ensure_future(self._fetch(key, minute, now))
                self._pending[ask].add_done_callback(lambda _: self._pending.pop(ask, None))
            # Shielded: one caller going away doesn't cancel the others' answer.
            found = (await asyncio.shield(self._pending[ask]))[1]
        if isinstance(found, _Failed):
            raise BoardUnavailable(found.message)
        return minute, cast(dict[str, Any], found)

    async def _fetch(
        self, key: BoardKey, minute: int, now: float
    ) -> tuple[int, dict[str, Any] | _Failed]:
        result: dict[str, Any] | _Failed
        asked = datetime.fromtimestamp(max(now, minute * 60), UK_TZ)
        try:
            board, platforms = await self.fetch(key.crs, key.to, key.platform)
            journeys = await self._journeys(key) if key.to and not board.services else []
            result = payload(board, platforms, key, asked, journeys)
        except ToolError as exc:
            result = _Failed(str(exc))
        # Kept to the end of its minute, when the pages ask for the next one.
        self._kept.set(key, (minute, result), min(self.ttl, (minute + 1) * 60 - now))
        return minute, result

    async def _journeys(self, key: BoardKey) -> list[Journey]:
        """The journeys with one change to the board's destination, if it has none direct.

        They are a bonus: without them the board says it has no trains.
        """
        if self.changes is None or key.to is None:
            return []
        try:
            return await self.changes(key.crs, key.to)
        except ToolError as exc:
            logger.info("No journeys with a change for %s: %s", key.path, exc)
            return []


# ----------------------------------------------------------------- the pages


def _minutes(hhmm: str) -> int | None:
    match = re.fullmatch(r"(\d\d):(\d\d)", hhmm)
    return int(match[1]) * 60 + int(match[2]) if match else None


def said(scheduled_at: str, expected: str) -> str:
    """Expected as a table says it, with how late the train is.

    Where Expected is a later time than scheduled it says how late
    ("15:44, 4 minutes late"), as the flaps show it ("15:44 +4", site.js
    running()). Past midnight counts forward; half a day or more the other way
    is early, and said as it is.
    """
    scheduled, due = _minutes(scheduled_at), _minutes(expected)
    if scheduled is None or due is None:
        return expected
    late = (due - scheduled) % 1440
    if late == 0 or late >= 720:
        return expected
    return f"{expected}, {late} minute{'' if late == 1 else 's'} late"


def unavailable(key: BoardKey) -> dict[str, Any]:
    """A board with no trains, for when it couldn't be fetched."""
    return {
        "station": {"name": station_name(key.crs), "crs": key.crs},
        "to": {"name": station_name(key.to), "crs": key.to} if key.to else None,
        "platform": key.platform,
        "platforms": [],
        "source": "darwin",
        "kind": key.kind(),
        "updated": "-",
        "messages": [],
        "trains": [],
    }


def clean_platform(text: str) -> str | None:
    """A platform as the board's address has it, or None if it can't be one."""
    text = re.sub(r"^(platform|plat|pl|p)[\s-]*", "", text.strip(), flags=re.I).upper()
    return text if PLATFORM.fullmatch(text) else None


def match(query: str) -> stations.Station | list[stations.Station]:
    """The one station `query` names, or the candidates if it names several.

    The candidates are an empty list if it names nothing.
    """
    if m := WITH_CODE.fullmatch(query):
        query = m.group(1)
    query = query.replace("-", " ").replace("_", " ").strip()
    try:
        return stations.resolve(query)
    except StationNotFound:
        return [s for s, score in stations.search(query, 5) if score >= CLOSE_ENOUGH]


def loose(station: str, then: str | None) -> BoardKey | None:
    """The board an address names with a station's name in place of a code.

    For example /board/cambridge or /board/LST/colchester, or a platform
    written "p9".
    """
    found = match(station)
    if not isinstance(found, stations.Station):
        return None
    if then is None:
        return BoardKey(found.crs)
    if key := parse(found.crs, then):
        return key
    to = match(then)
    if not isinstance(to, stations.Station):
        return None
    return BoardKey(found.crs, None if to.crs == found.crs else to.crs)


def then_field(then: str) -> str:
    """Which picker field an unreadable second part of an address goes back to."""
    return "platform" if len(then) <= 4 or any(c.isdigit() for c in then) else "to"
