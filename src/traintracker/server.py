"""traintracker MCP server: GB train times for Claude.

Run with `traintracker` (stdio). Other commands: `traintracker serve-http`,
`traintracker refresh`, `traintracker import FILE`, `traintracker status`,
`traintracker forget EMAIL`, `traintracker block EMAIL`.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import re
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal, ParamSpec, TypeVar

import httpx
import psycopg
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from traintracker import demo, http_app, planner, site, stations, ui
from traintracker.config import UK_TZ, Settings, load_dotenv
from traintracker.darwin import DarwinClient
from traintracker.errors import TrainTrackerError, UpstreamError
from traintracker.models import (
    Board,
    BoardService,
    CallingPoint,
    Journey,
    JourneyLeg,
    JourneyPlan,
    PlatformCheck,
    ServiceDetail,
    StationMatch,
    StationRef,
)
from traintracker.timetable import (
    Timetable,
    TimetableMissing,
    Trip,
    build_from_file,
    download_and_build,
    fmt_minutes,
    minutes_on,
)
from traintracker.usage import DarwinUsage

log = logging.getLogger("traintracker")
P = ParamSpec("P")
R = TypeVar("R")

INSTRUCTIONS = """\
GB (National Rail) train times.
- Resolve places with find_station when unsure; every tool also accepts names or CRS codes.
  If a tool says a name is ambiguous, ask the person which station they mean.
- "Next train", "is it on time": live_departures (live_arrivals for arrivals).
- "Show me the board", "display the departures": show_board. Only when the person asks to
  see a board; it draws one on screen where the app can.
- "Which platform is my train?": departure_platform. "What's leaving from platform 4?":
  platform_departures. A 'booked' platform_source is the timetabled platform, not yet confirmed.
- A future date/time, or "what trains are there": timetable.
- Getting from A to B, including changes: plan_journey. It uses the timetable and adds
  live times for today's trains where available.
- More about one train (all stops, delays): service_details with a service_id from any tool.
- If something isn't configured, data_status explains what's missing.
- Questions about privacy, or what is kept about the person: privacy_policy.
Times are UK local.
Sources, to credit when you say where the information comes from: live times are
"Powered by National Rail Enquiries"; timetable times are from Network Rail data feeds
(contains public sector information licensed under the Open Government Licence v3.0)."""


# --------------------------------------------------------------------- state


@dataclass
class App:
    settings: Settings
    http: httpx.AsyncClient
    darwin: DarwinClient
    usage: DarwinUsage | None = None
    refresh_task: asyncio.Task[Any] | None = None
    refresh_error: str | None = None
    refresh_started: datetime | None = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def timetable(self) -> Timetable:
        try:
            return Timetable.open(self.settings.timetable_db)
        except TimetableMissing:
            if self.refresh_task and not self.refresh_task.done():
                started = self.refresh_started.strftime("%H:%M") if self.refresh_started else "?"
                raise TimetableMissing(
                    f"The timetable is downloading (started {started}); try again in a few minutes."
                ) from None
            if self.refresh_error:
                raise TimetableMissing(
                    f"No timetable: the last download failed ({self.refresh_error})."
                ) from None
            raise

    def timetable_age(self) -> timedelta | None:
        return timetable_age(self.settings)

    def maybe_refresh(self) -> None:
        """Start a background download if the timetable is missing or stale.

        In demo mode, rebuild the generated timetable once a day instead.
        """
        if self.refresh_task and not self.refresh_task.done():
            return
        if not self.settings.auto_refresh or not self.settings.database_url:
            return
        if self.settings.demo:
            if demo.is_current(self.settings):
                return
        else:
            if not self.settings.has_nr:
                return
            age = self.timetable_age()
            if age is not None and age < timedelta(hours=self.settings.timetable_max_age_hours):
                return
        self.refresh_started = datetime.now(UK_TZ)
        self.refresh_task = asyncio.create_task(self._refresh())

    async def _refresh(self) -> None:
        try:
            if self.settings.demo:
                meta = await asyncio.to_thread(demo.build_timetable, self.settings)
            else:
                meta = await download_and_build(self.settings)
            self.refresh_error = None
            log.info("Timetable refreshed: %s", meta)
        except Exception as exc:  # keep serving with the old timetable
            self.refresh_error = str(exc)
            log.warning("Timetable refresh failed: %s", exc)


def timetable_age(settings: Settings) -> timedelta | None:
    """Time since the timetable was built; None if there is none or it can't be read."""
    try:
        built = Timetable.open(settings.timetable_db).meta.get("built_at")
    except (TrainTrackerError, psycopg.Error):
        return None
    return datetime.now(UK_TZ) - datetime.fromisoformat(built) if built else None


_app: App | None = None


def app() -> App:
    if _app is None:
        raise RuntimeError("Server not started")
    return _app


@asynccontextmanager
async def lifespan(_server: MCPServer[None]) -> AsyncIterator[None]:
    global _app
    load_dotenv()
    settings = Settings.from_env()
    if settings.demo:
        await asyncio.to_thread(demo.ensure_timetable, settings)
    async with httpx.AsyncClient(
        headers={"User-Agent": "traintracker/0.1"},
        # Demo mode answers Darwin requests in-process; nothing leaves the machine.
        transport=demo.transport(settings) if settings.demo else None,
    ) as http:
        usage = darwin_usage(settings)
        _app = App(
            settings=settings,
            http=http,
            darwin=DarwinClient(settings, http, usage),
            usage=usage,
        )
        _app.maybe_refresh()
        # Building a day's network takes a few seconds; do today's up front.
        prewarm = asyncio.create_task(asyncio.to_thread(_prewarm, settings))
        try:
            yield
        finally:
            prewarm.cancel()
            if _app.refresh_task:
                _app.refresh_task.cancel()
            if usage:
                await usage.flush(force=True)  # counts not yet written
            _app = None


def darwin_usage(settings: Settings) -> DarwinUsage | None:
    """The Darwin request counter; None where no request reaches Darwin (demo
    mode, no key for any product) or there is no database to keep the count in."""
    if settings.demo or not settings.has_any_darwin or not settings.database_url:
        return None
    return DarwinUsage(settings.database_url, settings.usage_schema)


def _prewarm(settings: Settings) -> None:
    try:
        tt = Timetable.open(settings.timetable_db)
        planner.network(tt, tt.meta.get("built_at"), datetime.now(UK_TZ).date())
    except TrainTrackerError:
        pass  # no timetable yet
    except Exception:  # never let a background warm-up crash the server
        log.exception("Prewarming today's journey network failed")


mcp: MCPServer[None] = MCPServer(
    "traintracker", instructions=INSTRUCTIONS, lifespan=lifespan, extensions=[ui.apps()]
)


# ------------------------------------------------------------------- helpers


def _station(query: str) -> stations.Station:
    return stations.resolve(query)


def _when(day: str | None, time: str | None) -> datetime:
    now = datetime.now(UK_TZ)
    text = (day or "today").strip().lower()
    if text == "today":
        d = now.date()
    elif text == "tomorrow":
        d = now.date() + timedelta(days=1)
    else:
        try:
            d = date.fromisoformat(text)
        except ValueError as exc:
            raise ToolError(f"Date '{day}' should be YYYY-MM-DD, 'today' or 'tomorrow'.") from exc
    if not time or time.strip().lower() == "now":
        if d == now.date():
            return now.replace(second=0, microsecond=0)
        return datetime.combine(d, datetime.min.time(), UK_TZ)
    try:
        hh, mm = time.strip().split(":")
        return datetime.combine(d, datetime.min.time(), UK_TZ).replace(hour=int(hh), minute=int(mm))
    except ValueError as exc:
        raise ToolError(f"Time '{time}' should be HH:MM (24-hour).") from exc


def _tool_errors(
    fn: Callable[P, Awaitable[R]],
) -> Callable[P, Awaitable[R]]:
    """Turn our errors into clean tool errors the model can relay."""

    @functools.wraps(fn)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        if _app is not None:
            _app.maybe_refresh()  # the server can run for days; keep the timetable current
        try:
            return await fn(*args, **kwargs)
        except ToolError:
            raise
        except TrainTrackerError as exc:
            raise ToolError(str(exc)) from exc
        except psycopg.Error as exc:
            log.exception("Timetable database error")
            raise ToolError(
                "The timetable database couldn't be read. Try again; if it keeps failing, run "
                "`traintracker refresh` to rebuild it."
            ) from exc
        except httpx.TimeoutException as exc:
            raise ToolError("The upstream service timed out; try again.") from exc
        except httpx.HTTPError as exc:
            raise ToolError(f"Network error reaching the data source: {exc}") from exc

    return wrapper


def _trip_board_service(
    trip: Trip, stop_index: int, day: date, board: Literal["departures", "arrivals"], points: bool
) -> BoardService:
    stop = trip.stops[stop_index]
    t = stop.dep if board == "departures" else stop.arr
    later = trip.stops[stop_index + 1 :] if board == "departures" else trip.stops[:stop_index]
    return BoardService(
        service_id=trip.service_id,
        source="timetable",
        operator=trip.operator,
        mode=trip.mode,
        origin=[_ref(trip.stops[0].crs)],
        destination=[_ref(trip.stops[-1].crs)],
        scheduled=fmt_minutes(minutes_on(day, trip, t)),
        platform=stop.platform,
        platform_source="booked" if stop.platform else None,
        status="scheduled",
        calling_points=[
            CallingPoint(
                station=_ref(s.crs),
                scheduled_arrival=fmt_minutes(s.arr),
                scheduled_departure=fmt_minutes(s.dep),
                platform=s.platform,
            )
            for s in later
        ]
        if points
        else None,
    )


def _ref(crs: str) -> StationRef:
    s = stations.by_crs(crs)
    return StationRef(name=s.name if s else crs, crs=crs)


def _timetable_board(
    tt: Timetable,
    st: stations.Station,
    when: datetime,
    window: int,
    board: Literal["departures", "arrivals"],
    other: stations.Station | None,
    rows: int,
    points: bool,
) -> Board:
    day = when.date()
    start = when.hour * 60 + when.minute
    hits = tt.trips_at(st.crs, day, start, start + window, arriving=board == "arrivals")
    services: list[tuple[int, BoardService]] = []
    for trip, stop in hits:
        idx = trip.stops.index(stop)
        if other:
            rest = trip.stops[idx + 1 :] if board == "departures" else trip.stops[:idx]
            if not any(s.crs == other.crs for s in rest):
                continue
        t = stop.dep if board == "departures" else stop.arr
        services.append(
            (minutes_on(day, trip, t) or 0, _trip_board_service(trip, idx, day, board, points))
        )
    services.sort(key=lambda x: x[0])
    notes = []
    valid_to = tt.meta.get("valid_to")
    if valid_to and day.isoformat() > valid_to:
        notes.append(f"The timetable only runs to {valid_to}; later dates may be incomplete.")
    notes.append("Booked (timetabled) times; engineering-work changes appear once published.")
    return Board(
        station=StationRef(name=st.name, crs=st.crs),
        board=board,
        date=day.isoformat(),
        source="timetable",
        filter=StationRef(name=other.name, crs=other.crs) if other else None,
        services=[s for _, s in services[:rows]],
        messages=notes,
    )


# --------------------------------------------------------------------- tools

LIVE, LOCAL = True, False


def _tool(
    title: str, open_world: bool, app: str | None = None
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Register a tool with its title and hints.

    Every tool only reads. The hints differ in one respect: whether a call can
    reach Darwin (open world) or stays within the server's own data. The
    timetable came from Network Rail, but a call reads the local copy, so it
    counts as closed. The title goes in two places because clients read either:
    the tool's own `title`, and `annotations.title`, which is what Claude's
    directory submission portal checks.

    `app` is the ui:// page a client that supports MCP Apps shows with every
    result of the tool. The result itself is the same with or without it.
    """
    return mcp.tool(
        title=title,
        meta={"ui": {"resourceUri": app}} if app else None,
        annotations=ToolAnnotations(
            title=title,
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=open_world,
        ),
    )


@_tool("Find a station", LOCAL)
async def find_station(
    query: Annotated[str, Field(description="Station name, partial name or 3-letter CRS code.")],
    limit: Annotated[int, Field(ge=1, le=20)] = 5,
) -> list[StationMatch]:
    """Find GB (National Rail) railway stations by name, part of a name or 3-letter CRS
    code. Returns the closest matches first, each with its name and CRS code."""
    return [
        StationMatch(name=s.name, crs=s.crs, score=round(sc, 1))
        for s, sc in stations.search(query, limit)
    ]


@_tool("Live departures", LIVE)
@_tool_errors
async def live_departures(
    station: Annotated[str, Field(description="Station name or CRS code.")],
    to: Annotated[str | None, Field(description="Only trains calling at this station.")] = None,
    rows: Annotated[int, Field(ge=1, le=50)] = 10,
    offset_minutes: Annotated[
        int, Field(ge=-119, le=119, description="Shift the board start (e.g. 30 = from +30 min).")
    ] = 0,
    include_calling_points: bool = False,
) -> Board:
    """Live departure board for a GB railway station, covering the next two hours:
    expected times, platforms, delays and cancellations. The data comes from Darwin
    (National Rail Enquiries). If Darwin is offline, the board shows booked timetable
    times and says so."""
    return await _live_board(
        app(),
        _station(station),
        "departures",
        _station(to) if to else None,
        rows,
        offset_minutes,
        include_calling_points,
    )


async def _live_board(
    a: App,
    st: stations.Station,
    board: Literal["departures", "arrivals"],
    other: stations.Station | None,
    rows: int,
    offset_minutes: int,
    points: bool,
    until: Callable[[list[BoardService]], bool] | None = None,
) -> Board:
    """Darwin, else booked times with a note saying why live data is missing.

    `until` lets Darwin stop paging once enough services have been seen.
    """
    notes: list[str] = []
    other_crs = other.crs if other else None
    start = datetime.now(UK_TZ) + timedelta(minutes=offset_minutes)
    b: Board | None = None
    darwin_ok = a.settings.has_darwin if board == "departures" else a.settings.has_darwin_arrivals
    if darwin_ok:
        try:
            b = await a.darwin.board(
                st.crs,
                board,
                filter_crs=other_crs,
                rows=rows,
                offset_minutes=offset_minutes,
                until=until,
            )
        except (UpstreamError, httpx.HTTPError) as exc:
            notes.append(_darwin_offline(exc))
    if b is None:
        try:
            tt = a.timetable()
        except TimetableMissing as exc:
            if notes:
                raise UpstreamError(
                    f"{notes[0]} Booked times aren't available either: {exc}"
                ) from exc
            raise
        b = _timetable_board(tt, st, start, 120, board, other, rows, points)
        notes.append(
            "Showing booked times instead."
            if notes
            else "No live source is configured for this board; showing booked times."
        )
    b.messages[:0] = notes
    if not points:
        for s in b.services:
            s.calling_points = None
    return b


def _darwin_offline(exc: Exception) -> str:
    return (
        f"National Rail live data (Darwin) is offline ({_why(exc)}). "
        "Delays, cancellations and live platforms are not available."
    )


def _why(exc: Exception) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return "timed out"
    return str(exc) or type(exc).__name__


@_tool("Live arrivals", LIVE)
@_tool_errors
async def live_arrivals(
    station: Annotated[str, Field(description="Station name or CRS code.")],
    from_station: Annotated[
        str | None, Field(description="Only trains that called at this station earlier.")
    ] = None,
    rows: Annotated[int, Field(ge=1, le=50)] = 10,
    include_calling_points: bool = False,
) -> Board:
    """Live arrivals board for a GB railway station, covering the next two hours. The
    data comes from Darwin (National Rail Enquiries) where the server has its arrivals
    feed; otherwise the board shows booked timetable times and says so."""
    return await _live_board(
        app(),
        _station(station),
        "arrivals",
        _station(from_station) if from_station else None,
        rows,
        0,
        include_calling_points,
    )


@_tool("Show a departure board", LIVE, app=ui.BOARD_URI)
@_tool_errors
async def show_board(
    station: Annotated[str, Field(description="Station name or CRS code.")],
    board: Annotated[
        Literal["departures", "arrivals"], Field(description="Which board to show.")
    ] = "departures",
    calling_at: Annotated[
        str | None,
        Field(
            description="Only trains going on to this station (departures) or coming from "
            "it (arrivals)."
        ),
    ] = None,
    rows: Annotated[int, Field(ge=1, le=12)] = 10,
) -> Board:
    """Draw a GB railway station's live departures or arrivals on screen as a departure
    board. Use this only when the person asks to see, show or display a board. For any
    other question about trains use live_departures or live_arrivals: they return the same
    trains without drawing anything. An app that can't draw the board gets the same
    answer as from those tools."""
    other = _station(calling_at) if calling_at else None
    return await _live_board(app(), _station(station), board, other, rows, 0, False)


@_tool("Departure platform", LIVE)
@_tool_errors
async def departure_platform(
    station: Annotated[str, Field(description="Station name or CRS code.")],
    to: Annotated[str | None, Field(description="Only trains calling at this station.")] = None,
    time: Annotated[
        str | None, Field(description="Booked departure HH:MM (24h). Default: the next train.")
    ] = None,
) -> PlatformCheck:
    """The platform one train leaves from at a GB railway station: the next departure,
    or the one booked at a given time. Large stations often announce the live platform
    about 10 minutes before departure; until then the booked (timetable) platform is
    returned and flagged as 'booked'."""
    a = app()
    st = _station(station)
    other = _station(to) if to else None
    offset = 0
    if time:
        target = _when(None, time)
        offset = max(-119, min(119, _minutes_until(target.strftime("%H:%M")) - 1))
    b = await _live_board(a, st, "departures", other, 10, offset, False)
    _fill_booked_platforms(a, st.crs, b.services)
    candidates = [s for s in b.services if not time or s.scheduled == time]
    if not candidates:
        where = f" to {other.name}" if other else ""
        when = f" at {time}" if time else " in the next two hours"
        raise ToolError(f"No departure from {st.name}{where}{when}.")
    svc = candidates[0]
    note = _platform_note(svc, b)
    if b.source == "timetable" and b.messages:
        note = f"{b.messages[0]} {note}"  # why there is no live platform
    return PlatformCheck(
        station=b.station,
        service=svc,
        platform=svc.platform,
        platform_source=svc.platform_source,
        minutes_to_departure=_minutes_until(_departure_hhmm(svc)),
        note=note,
    )


def _platform_note(svc: BoardService, b: Board) -> str:
    if svc.status == "cancelled":
        return "This train is cancelled."
    if svc.platform_source == "live":
        return f"Platform {svc.platform}."
    if svc.platform:
        return (
            f"Booked for platform {svc.platform}; not yet confirmed by the live feed. "
            "Check again nearer departure."
        )
    if b.platform_available is False:
        return "This station does not publish platform numbers."
    return "Platform not yet announced. Check again nearer departure."


@_tool("Departures from a platform", LIVE)
@_tool_errors
async def platform_departures(
    station: Annotated[str, Field(description="Station name or CRS code.")],
    platform: Annotated[str, Field(description="Platform, e.g. '4' or '9B'.")],
    count: Annotated[int, Field(ge=1, le=10)] = 3,
) -> Board:
    """The next trains leaving from one platform of a GB railway station in the next two
    hours. A train whose live platform isn't announced yet is matched on its booked
    platform (platform_source 'booked'), which can still change."""
    a = app()
    st = _station(station)
    want = _norm_platform(platform)

    def matches(s: BoardService) -> bool:
        return bool(s.platform) and _norm_platform(s.platform or "") == want

    def enough(services: list[BoardService]) -> bool:
        _fill_booked_platforms(a, st.crs, services)
        return sum(map(matches, services)) >= count

    b = await _live_board(a, st, "departures", None, 150, 0, False, until=enough)
    _fill_booked_platforms(a, st.crs, b.services)
    seen = sorted({s.platform for s in b.services if s.platform}, key=_platform_sort)
    b.services = [s for s in b.services if matches(s)][:count]
    if not b.services:
        b.messages.append(
            f"No departures from platform {platform} in the next two hours."
            + (f" Platforms in use: {', '.join(seen)}." if seen else "")
        )
    elif any(s.platform_source == "booked" for s in b.services):
        b.messages.append(
            "Trains marked platform_source 'booked' use the timetabled platform; "
            "the live platform may differ once announced."
        )
    return b


def _fill_booked_platforms(a: App, crs: str, services: list[BoardService]) -> None:
    """Give departures with no announced platform their timetabled one."""
    missing = [s for s in services if not s.platform and s.scheduled]
    if not missing:
        return
    try:
        tt = a.timetable()
    except TrainTrackerError:
        return  # booked platforms are a bonus
    now = datetime.now(UK_TZ)
    start = now.hour * 60 + now.minute
    by_key: dict[tuple[str, str], str] = {}
    by_time: dict[str, set[str]] = {}
    for trip, stop in tt.trips_at(crs, now.date(), start - 60, start + 180):
        hhmm = fmt_minutes(minutes_on(now.date(), trip, stop.dep))
        if not hhmm or not stop.platform:
            continue
        by_key[(hhmm, trip.stops[-1].crs)] = stop.platform
        by_time.setdefault(hhmm, set()).add(stop.platform)
    for s in missing:
        assert s.scheduled is not None
        plat = next(
            (by_key[k] for d in s.destination if (k := (s.scheduled, d.crs or "")) in by_key), None
        )
        if plat is None and len(by_time.get(s.scheduled, ())) == 1:
            plat = next(iter(by_time[s.scheduled]))
        if plat:
            s.platform, s.platform_source = plat, "booked"


def _norm_platform(value: str) -> str:
    return re.sub(r"^(platform|plat|pl|p)\s*", "", value.strip(), flags=re.I).upper()


def _platform_sort(value: str) -> tuple[int, str]:
    digits = re.match(r"\d+", value)
    return (int(digits.group()) if digits else 10_000, value)


def _departure_hhmm(s: BoardService) -> str | None:
    exp = (s.expected or "").rstrip("*")
    return exp if re.fullmatch(r"\d{2}:\d{2}", exp) else s.scheduled


def _minutes_until(hhmm: str | None) -> int:
    """Minutes from now to HH:MM, taking times up to 12 hours back as in the past."""
    if not hhmm:
        return 0
    now = datetime.now(UK_TZ)
    h, m = hhmm.split(":")
    diff = (int(h) * 60 + int(m) - (now.hour * 60 + now.minute)) % (24 * 60)
    return diff - 24 * 60 if diff > 12 * 60 else diff


@_tool("Timetable", LOCAL)
@_tool_errors
async def timetable(
    station: Annotated[str, Field(description="Station name or CRS code.")],
    date: Annotated[str | None, Field(description="YYYY-MM-DD, 'today' or 'tomorrow'.")] = None,
    time: Annotated[
        str | None,
        Field(description="Start time HH:MM (24h). Default: now today, else the whole day."),
    ] = None,
    to: Annotated[str | None, Field(description="Only trains later calling here.")] = None,
    from_station: Annotated[
        str | None, Field(description="Only trains earlier calling here.")
    ] = None,
    board: Literal["departures", "arrivals"] = "departures",
    window_minutes: Annotated[int, Field(ge=10, le=1440)] = 180,
    rows: Annotated[int, Field(ge=1, le=100)] = 20,
    include_calling_points: bool = False,
) -> Board:
    """Booked (timetabled) departures or arrivals at a GB railway station on any date the
    Network Rail timetable covers, usually months ahead. For future dates; for trains in
    the next two hours, live_departures has live times."""
    a = app()
    st = _station(station)
    other_q = to if board == "departures" else from_station
    other = _station(other_q) if other_q else None
    when = _when(date, time)
    if not time and when.date() != datetime.now(UK_TZ).date():
        window_minutes = 1440  # "what runs on Saturday?" means the whole day
    b = _timetable_board(
        a.timetable(), st, when, window_minutes, board, other, rows, include_calling_points
    )
    if a.settings.demo:
        b.messages.insert(0, demo.DEMO_NOTE)
    return b


@_tool("Service details", LIVE)
@_tool_errors
async def service_details(
    service_id: Annotated[
        str, Field(description="A service_id from another tool (darwin:… or tt:…).")
    ],
) -> ServiceDetail:
    """Every stop of one GB train, with booked times and, for a train taken from a live
    board, expected and actual times. Takes a service_id returned by another tool."""
    a = app()
    if service_id.startswith("darwin:"):
        return await a.darwin.service(service_id.removeprefix("darwin:"))
    if service_id.startswith("tt:"):
        try:
            _, uid, run = service_id.split(":")
            run_date = date.fromisoformat(run)
        except ValueError as exc:
            raise ToolError(f"'{service_id}' is not a valid timetable service ID.") from exc
        trip = a.timetable().trip(uid, run_date)
        if trip is None:
            raise ToolError(f"No timetabled service {uid} runs on {run}.")
        return ServiceDetail(
            service_id=service_id,
            source="timetable",
            operator=trip.operator,
            headcode=trip.headcode,
            date=run,
            mode=trip.mode,
            origin=[_ref(trip.stops[0].crs)],
            destination=[_ref(trip.stops[-1].crs)],
            calling_points=[
                CallingPoint(
                    station=_ref(s.crs),
                    scheduled_arrival=fmt_minutes(s.arr),
                    scheduled_departure=fmt_minutes(s.dep),
                    platform=s.platform,
                )
                for s in trip.stops
            ],
        )
    raise ToolError("service_id must start with darwin: or tt:.")


@_tool("Plan a journey", LIVE)
@_tool_errors
async def plan_journey(
    origin: Annotated[str, Field(description="Start station name or CRS code.")],
    destination: Annotated[str, Field(description="End station name or CRS code.")],
    date: Annotated[str | None, Field(description="YYYY-MM-DD, 'today' or 'tomorrow'.")] = None,
    time: Annotated[str | None, Field(description="Depart at or after HH:MM. Default now.")] = None,
    via: Annotated[str | None, Field(description="Force a change at this station.")] = None,
    count: Annotated[int, Field(ge=1, le=6)] = 3,
    max_changes: Annotated[int, Field(ge=0, le=6)] = 4,
    live: Annotated[bool, Field(description="Overlay Darwin live times for today.")] = True,
) -> JourneyPlan:
    """Plan rail journeys between two GB stations, with changes up to max_changes.
    Returns the fastest options first plus the fewest-changes option. Transfers between
    London terminals are included as approximate walk/Tube links. For today's trains in
    the next two hours, live Darwin times are added and at-risk connections flagged."""
    a = app()
    o, d = _station(origin), _station(destination)
    if o.crs == d.crs:
        raise ToolError(f"Origin and destination are both {o.name}.")
    v = _station(via) if via else None
    if v and v.crs in (o.crs, d.crs):
        v = None  # "via" an end point is no constraint
    when = _when(date, time)
    tt = a.timetable()
    mct = a.settings.min_interchange_minutes
    journeys = await asyncio.to_thread(
        planner.plan,
        tt,
        o.crs,
        d.crs,
        when,
        via=v.crs if v else None,
        count=count,
        max_changes=max_changes,
        mct=mct,
    )
    notes = ["Times are booked (timetable) times unless a leg shows expected times."]
    if a.settings.demo:
        notes.insert(0, demo.DEMO_NOTE)
    if any(leg.mode == "tube (approx.)" for j in journeys for leg in j.legs):
        notes.append("Tube links are estimates (~12 min + 3.5 min/km); check TfL status.")
    if not journeys:
        notes.append(
            "No journey found before the end of the service day. Try an earlier time, "
            "tomorrow, or a via station."
        )
    valid_to = tt.meta.get("valid_to")
    if valid_to and when.date().isoformat() > valid_to:
        notes.append(f"The timetable only runs to {valid_to}.")
    if live and a.settings.has_darwin and when.date() == datetime.now(UK_TZ).date():
        await _overlay_live(a, journeys, mct, notes)
    return JourneyPlan(
        origin=StationRef(name=o.name, crs=o.crs),
        destination=StationRef(name=d.name, crs=d.crs),
        searched_from=when.isoformat(timespec="minutes"),
        journeys=journeys,
        interchanges_considered=[StationRef(name=v.name, crs=v.crs)] if v else [],
        notes=notes,
    )


async def _overlay_live(a: App, journeys: list[Journey], mct: int, notes: list[str]) -> None:
    """Add Darwin expected times to legs departing in the next ~2 hours."""
    now = datetime.now(UK_TZ)
    boards: dict[tuple[str, str], Board | None] = {}
    failure: Exception | None = None

    async def board_for(frm: str, to: str) -> Board | None:
        nonlocal failure
        key = (frm, to)
        if key not in boards:
            try:
                boards[key] = await a.darwin.board(frm, "departures", filter_crs=to, rows=20)
            except (TrainTrackerError, httpx.HTTPError) as exc:
                # Live times are a bonus; the plan stands without them.
                log.info("Live overlay skipped for %s->%s: %s", frm, to, exc)
                boards[key] = None
                failure = failure or exc
        return boards[key]

    touched = False
    for j in journeys:
        for leg in j.legs:
            if not leg.service_id or not leg.board_at.crs or not leg.alight_at.crs:
                continue
            dep = datetime.fromisoformat(leg.depart_scheduled)
            if not (now - timedelta(minutes=15) <= dep <= now + timedelta(minutes=115)):
                continue
            b = await board_for(leg.board_at.crs, leg.alight_at.crs)
            same_time = [
                s for s in (b.services if b else []) if s.scheduled == dep.strftime("%H:%M")
            ]
            # Two trains in the same minute (e.g. portions): prefer the same destination.
            match = next(
                (s for s in same_time if any(x.name == leg.destination for x in s.destination)),
                same_time[0] if len(same_time) == 1 else None,
            )
            if not match:
                continue
            touched = True
            leg.depart_expected = match.expected
            leg.cancelled = match.status == "cancelled"
            leg.platform = match.platform or leg.platform
            for cp in match.calling_points or []:
                if cp.station.crs == leg.alight_at.crs:
                    leg.arrive_expected = cp.actual or cp.expected
                    break
        j.connection_at_risk = _at_risk(j, mct)
    if touched:
        notes.append("Live times from National Rail (Darwin) added where available.")
    if failure:
        scope = "Legs without live times show booked times." if touched else "Times are booked."
        notes.append(f"{_darwin_offline(failure)} {scope}")


def _live_time(scheduled_iso: str, expected: str | None) -> datetime | None:
    """Expected datetime for a leg time; None if unknown (Delayed/No report)."""
    sched = datetime.fromisoformat(scheduled_iso)
    if not expected or expected.lower().startswith("on time"):
        return sched
    exp = expected.rstrip("*")
    if len(exp) == 5 and exp[2] == ":":
        t = sched.replace(hour=int(exp[:2]), minute=int(exp[3:]))
        if t < sched - timedelta(hours=12):
            t += timedelta(days=1)
        elif t > sched + timedelta(hours=12):
            t -= timedelta(days=1)
        return t
    return None


def _expected_arrival(leg: JourneyLeg) -> datetime | None:
    """Best estimate of arrival; None when live data says 'Delayed' with no time."""
    if leg.arrive_expected:
        return _live_time(leg.arrive_scheduled, leg.arrive_expected)
    arrive = datetime.fromisoformat(leg.arrive_scheduled)
    if not leg.depart_expected:
        return arrive
    # No arrival forecast: assume the departure delay carries through.
    dep_sched = datetime.fromisoformat(leg.depart_scheduled)
    dep = _live_time(leg.depart_scheduled, leg.depart_expected)
    return None if dep is None else arrive + max(dep - dep_sched, timedelta())


def _at_risk(j: Journey, mct: int) -> bool:
    """True if a cancellation or live delay breaks a connection.

    Walk/Tube links carry the delay through: arriving late at Kings Cross makes
    you late at Liverpool Street too.
    """
    if any(leg.cancelled for leg in j.legs):
        return True
    ready: datetime | None = None  # when you can catch the next train
    unknown = False  # a previous train is "Delayed" with no estimate
    after_ride = False
    for leg in j.legs:
        if not leg.service_id:  # walk/Tube, already padded for access time
            if ready is not None:
                span = datetime.fromisoformat(leg.arrive_scheduled) - datetime.fromisoformat(
                    leg.depart_scheduled
                )
                ready += span
            after_ride = False
            continue
        if unknown:
            return True
        dep = _live_time(leg.depart_scheduled, leg.depart_expected)
        if ready is not None:
            need = timedelta(minutes=mct) if after_ride else timedelta()
            if dep is None or dep - ready < need:
                return True
        ready = _expected_arrival(leg)
        unknown = ready is None
        after_ride = True
    return False


@_tool("Data status", LOCAL)
async def data_status() -> dict[str, Any]:
    """Which data sources this server has configured, how fresh its timetable is, how
    much of the Darwin request allowance has been used, and what is missing. Takes no
    arguments."""
    a = app()
    s = a.settings
    age = a.timetable_age()
    meta: dict[str, Any] = {}
    try:
        meta = dict(a.timetable().meta)
    except TrainTrackerError as exc:
        meta = {"error": str(exc)}
    status: dict[str, Any] = {
        "darwin_live_departures": "configured" if s.has_darwin else "missing DARWIN_API_KEY",
        "darwin_arrivals": "configured" if s.has_darwin_arrivals else "not configured (optional)",
        "network_rail_timetable": {
            "credentials": "configured" if s.has_nr else "missing NR_USERNAME / NR_PASSWORD",
            "database": "configured" if s.database_url else "missing DATABASE_URL",
            "schema": s.timetable_db.schema,
            "auto_refresh": s.auto_refresh,
            "age_hours": round(age.total_seconds() / 3600, 1) if age else None,
            "refreshing": bool(a.refresh_task and not a.refresh_task.done()),
            "last_refresh_error": a.refresh_error,
            **meta,
        },
        "min_interchange_minutes": s.min_interchange_minutes,
    }
    if a.usage:
        status["darwin_usage"] = await _usage_status(a.usage)
    if s.demo:
        # Replace the account details: none are used in demo mode.
        status = {
            "demo_mode": demo.DEMO_NOTE + " Unset TRAINTRACKER_DEMO to use real data.",
            "demo_stations": sorted(set(demo.TIPLOCS.values())),
            "network_rail_timetable": status["network_rail_timetable"],
            "min_interchange_minutes": s.min_interchange_minutes,
        }
    return status


@_tool("Privacy policy", LOCAL)
async def privacy_policy() -> dict[str, Any]:
    """This service's privacy policy: what it processes about the person using it (such
    as their email address at sign-in), why, how long it is kept, who processes it, and
    how to ask for a copy or deletion. Use it to answer questions about privacy or data
    held. Takes no arguments."""
    # The text comes from the /privacy page itself, so the two can't disagree.
    policy: dict[str, Any] = {"policy": site.page_text("/privacy")}
    url = app().settings.public_url
    if not url.startswith("http://localhost"):  # a stdio server has no public page
        policy["url"] = f"{url}/privacy"
    return policy


async def _usage_status(usage: DarwinUsage) -> dict[str, Any]:
    try:
        return (await asyncio.to_thread(usage.read)).as_dict()
    except psycopg.Error as exc:
        return {"error": f"The usage count couldn't be read: {exc}"}


# ----------------------------------------------------------------------- CLI


def configure_logging() -> None:
    # stdout carries the MCP protocol; logs must go to stderr.
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(message)s")
    # httpx logs every request URL at INFO. The Network Rail download redirects to a
    # presigned S3 URL whose query string holds temporary AWS credentials.
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    configure_logging()
    cmd = argv[0] if argv else "serve"
    load_dotenv()
    settings = Settings.from_env()
    if cmd == "serve":
        mcp.run("stdio")
    elif cmd == "serve-http":
        if problem := _sign_in_problem(settings):
            print(problem, file=sys.stderr)
            sys.exit(2)
        http_app.serve(http_app.build_app(mcp, settings), settings.host, settings.port)
    elif cmd in ("forget", "block") and len(argv) == 2:
        _account_command(cmd, argv[1], settings)
    elif cmd == "refresh":
        meta = (
            demo.build_timetable(settings)
            if settings.demo
            else asyncio.run(download_and_build(settings))
        )
        print(f"Timetable built in schema {settings.timetable_db.schema}: {meta}", file=sys.stderr)
    elif cmd == "import" and len(argv) == 2:
        meta = build_from_file(Path(argv[1]), settings.timetable_db)
        print(f"Timetable built in schema {settings.timetable_db.schema}: {meta}", file=sys.stderr)
    elif cmd == "status":
        info: Any
        try:
            info = Timetable.open(settings.timetable_db).meta
        except TrainTrackerError as exc:
            info = str(exc)
        print(
            ("Demo mode (generated data)\n" if settings.demo else "")
            + f"Darwin: {_yn(settings.has_darwin)}; NR: {_yn(settings.has_nr)}\n"
            + _usage_line(settings)
            + f"Timetable schema {settings.timetable_db.schema}: {info}",
            file=sys.stderr,
        )
    else:
        print(
            "usage: traintracker [serve | serve-http | refresh | import FILE.json.gz | status"
            " | forget EMAIL | block EMAIL]",
            file=sys.stderr,
        )
        sys.exit(2)


def _sign_in_problem(settings: Settings) -> str | None:
    """Why serve-http can't start with these settings; None if it can."""
    if missing := settings.email_sign_in_missing:
        return f"Email sign-in is partly configured: also set {', '.join(missing)}."
    if settings.mcp_auth_token or settings.oauth_passphrase or settings.email_sign_in:
        return None
    return (
        "serve-http needs a way to sign in: email codes (RESEND_API_KEY, MAIL_FROM and "
        "MCP_ACCOUNT_SECRET), MCP_OAUTH_PASSPHRASE, MCP_AUTH_TOKEN (static bearer token), "
        "or any mix of them."
    )


def _account_command(cmd: str, address: str, settings: Settings) -> None:
    """`forget` answers an erasure request; `block` shuts out an abusive account."""
    if not settings.account_secret:
        print("MCP_ACCOUNT_SECRET is not set, so there are no email accounts.", file=sys.stderr)
        sys.exit(2)
    provider = http_app.build_provider(settings)
    provider.create_tables()
    if cmd == "block":
        provider.block(address)
        print("Blocked: the address can't sign in and its tokens no longer work.", file=sys.stderr)
    elif provider.forget(address):
        print("Forgotten: the account and its tokens are deleted.", file=sys.stderr)
    else:
        print("No account for that address.", file=sys.stderr)


def _usage_line(settings: Settings) -> str:
    usage = darwin_usage(settings)
    if not usage:
        return ""
    try:
        return f"Darwin usage: {usage.read().line()}\n"
    except psycopg.Error as exc:
        return f"Darwin usage: couldn't be read ({exc})\n"


def _yn(flag: bool) -> str:
    return "yes" if flag else "no"


if __name__ == "__main__":
    main()
