"""Network Rail SCHEDULE feed -> Postgres timetable, and queries over it.

The feed (CIF_ALL_FULL_DAILY, JSON) is newline-delimited JSON with records:
  {"JsonTimetableV1": {...}}  header
  {"TiplocV1": {...}}         location reference: tiploc_code -> crs_code
  {"JsonAssociationV1": ...}  joins/divides (ignored)
  {"JsonScheduleV1": {...}}   one schedule record per UID/date-range/STP

A train (CIF_train_uid) can have several overlapping records. For a given day
the one that applies is picked by STP indicator: C (cancelled) > N > O > P.
We keep public passenger stops only (those with a public time), which keeps
the tables small (a few hundred MB) and every query fast.

The timetable lives in one Postgres schema per TimetableDB. A refresh builds a
new schema and swaps it in with a rename, so readers never see a partial
timetable. The tables are UNLOGGED: they are rebuilt from the feed every day, so
they skip the write-ahead log (sparing a shared server's WAL and backups), and
Postgres empties them after a crash, which reads as "no timetable" until the
next refresh.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
import os
import tempfile
import threading
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, ClassVar

import httpx
import psycopg
from psycopg import sql
from psycopg.abc import QueryNoTemplate as Query

from traintracker.config import UK_TZ, Settings, TimetableDB
from traintracker.errors import NotConfigured, TrainTrackerError, UpstreamError

log = logging.getLogger(__name__)

# Categories that carry the public (incl. replacement buses and ships).
PASSENGER_CATEGORIES = {"OO", "OW", "XX", "XZ", "XC", "XI", "XD", "XR", "BR", "BS", "SS"}
STP_PRIORITY = {"C": 0, "N": 1, "O": 2, "P": 3}
BUS_CATEGORIES = {"BR", "BS"}
DAY = 24 * 60

OPERATORS = {
    "AW": "Transport for Wales",
    "CC": "c2c",
    "CH": "Chiltern Railways",
    "CS": "Caledonian Sleeper",
    "EM": "East Midlands Railway",
    "ES": "Eurostar",
    "GC": "Grand Central",
    "GN": "Great Northern",
    "GR": "LNER",
    "GW": "Great Western Railway",
    "GX": "Gatwick Express",
    "HT": "Hull Trains",
    "HX": "Heathrow Express",
    "IL": "Island Line",
    "LD": "Lumo",
    "LE": "Greater Anglia",
    "LM": "West Midlands Trains",
    "LO": "London Overground",
    "LT": "London Underground",
    "ME": "Merseyrail",
    "NT": "Northern",
    "SE": "Southeastern",
    "SN": "Southern",
    "SR": "ScotRail",
    "SW": "South Western Railway",
    "SX": "Stansted Express",
    "TL": "Thameslink",
    "TP": "TransPennine Express",
    "VT": "Avanti West Coast",
    "XC": "CrossCountry",
    "XR": "Elizabeth line",
}

# Created in the build schema (first on the search_path). Keys and indexes are
# added after the bulk load, which is faster than maintaining them row by row.
SCHEMA = """
CREATE UNLOGGED TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE UNLOGGED TABLE schedules (
    id INTEGER NOT NULL,
    uid TEXT NOT NULL,
    stp TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    days_runs TEXT NOT NULL,
    atoc TEXT,
    headcode TEXT,
    category TEXT,
    public INTEGER NOT NULL
);
CREATE UNLOGGED TABLE stops (
    schedule_id INTEGER NOT NULL,
    seq INTEGER NOT NULL,
    crs TEXT NOT NULL,
    arr INTEGER,
    dep INTEGER,
    platform TEXT
);
CREATE UNLOGGED TABLE raw_stops (
    schedule_id INTEGER, seq INTEGER, tiploc TEXT, arr INTEGER, dep INTEGER, platform TEXT
);
CREATE UNLOGGED TABLE tiplocs (tiploc TEXT PRIMARY KEY, crs TEXT);
"""

INDEXES = """
ALTER TABLE schedules ADD PRIMARY KEY (id);
ALTER TABLE stops ADD PRIMARY KEY (schedule_id, seq);
CREATE INDEX schedules_uid ON schedules (uid);
CREATE INDEX schedules_dates ON schedules (start_date, end_date);
CREATE INDEX stops_crs ON stops (crs, dep);
CREATE INDEX stops_crs_arr ON stops (crs, arr);
"""

# Serialises refreshes of one schema across processes (server and cron job).
_LOCK_SQL = "SELECT pg_try_advisory_lock(hashtext(%s))"
_UNLOCK_SQL = "SELECT pg_advisory_unlock(hashtext(%s))"


class TimetableMissing(NotConfigured):
    pass


# ------------------------------------------------------------------- parsing


def parse_time(value: str | None) -> int | None:
    """'0802' or '0802H' -> minutes after midnight (half-minutes dropped)."""
    if not value or len(value) < 4 or not value[:4].isdigit():
        return None
    return int(value[:2]) * 60 + int(value[2:4])


def _public_time(public: str | None, working: str | None) -> int | None:
    # In CIF a public time of 0000 usually means "not advertised". Keep it only
    # when the working time is within a couple of minutes of midnight.
    if public == "0000" and (working or "")[:4] not in {"2358", "2359", "0000", "0001", "0002"}:
        return None
    return parse_time(public)


@dataclass(slots=True)
class ParsedStop:
    tiploc: str
    arr: int | None
    dep: int | None
    platform: str | None


def parse_schedule(rec: dict[str, Any]) -> tuple[dict[str, Any], list[ParsedStop]] | None:
    if rec.get("transaction_type", "Create") != "Create":
        return None
    seg = rec.get("schedule_segment") or {}
    stp = rec.get("CIF_stp_indicator") or "P"
    category = seg.get("CIF_train_category")
    public = category in PASSENGER_CATEGORIES
    if not public and stp not in ("C", "O"):
        # Freight, ECS etc. Overlays/cancellations are kept even when not
        # passenger: they can stop a passenger train running on a given day.
        return None
    row = {
        "uid": rec.get("CIF_train_uid"),
        "stp": stp,
        "start_date": rec.get("schedule_start_date"),
        "end_date": rec.get("schedule_end_date") or rec.get("schedule_start_date"),
        "days_runs": rec.get("schedule_days_runs") or "1111111",
        "atoc": rec.get("atoc_code"),
        "headcode": seg.get("signalling_id") or seg.get("CIF_headcode"),
        "category": category,
        "public": int(public),
    }
    if not row["uid"] or not row["start_date"]:
        return None
    stops: list[ParsedStop] = []
    if public:
        last = -1
        rollover = 0

        def monotonic(value: int | None) -> int | None:
            # Times only go forwards within a schedule; a step back means midnight.
            nonlocal last, rollover
            if value is None:
                return None
            adjusted = value + rollover
            # Only a big step back is midnight; small ones are data noise.
            if adjusted < last - 6 * 60:
                rollover += DAY
                adjusted += DAY
            last = adjusted
            return adjusted

        for loc in seg.get("schedule_location") or []:
            arr = _public_time(loc.get("public_arrival"), loc.get("arrival"))
            dep = _public_time(loc.get("public_departure"), loc.get("departure"))
            if arr is None and dep is None:
                continue
            arr = monotonic(arr)
            dep = monotonic(dep)
            stops.append(ParsedStop(loc.get("tiploc_code") or "", arr, dep, loc.get("platform")))
        if len(stops) < 2:
            row["public"] = 0
            stops = []
    return row, stops


def iter_records(lines: Iterable[str | bytes]) -> Iterator[dict[str, Any]]:
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            log.warning("Skipping malformed feed line")


def build(
    lines: Iterable[str | bytes], db: TimetableDB, today: date | None = None
) -> dict[str, Any]:
    """Build a fresh timetable from feed lines, then swap it in for db.schema."""
    today = today or datetime.now(UK_TZ).date()
    keep_from = (today - timedelta(days=2)).isoformat()
    staging = f"{db.schema}_build"
    live, build_schema = sql.Identifier(db.schema), sql.Identifier(staging)
    with _connect(db.dsn, autocommit=True) as con:
        if not con.execute(_LOCK_SQL, (db.schema,)).fetchone()[0]:  # type: ignore[index]
            raise TrainTrackerError("A timetable refresh is already running.")
        try:
            con.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(build_schema))
            con.execute(sql.SQL("CREATE SCHEMA {}").format(build_schema))
            con.execute(sql.SQL("SET search_path TO {}").format(build_schema))
            con.execute(SCHEMA)
            header, n_schedules, n_public = _load(con, db.dsn, staging, lines, keep_from)
            if n_public == 0:
                raise TrainTrackerError(
                    "The feed contained no passenger schedules; keeping old DB."
                )
            # Resolve TIPLOC -> CRS; stops at locations without a CRS are dropped.
            con.execute(
                """INSERT INTO stops
                   SELECT r.schedule_id, r.seq, t.crs, r.arr, r.dep, r.platform
                   FROM raw_stops r JOIN tiplocs t ON t.tiploc = r.tiploc"""
            )
            con.execute("DROP TABLE raw_stops, tiplocs")
            con.execute(INDEXES)
            con.execute("ANALYZE schedules; ANALYZE stops")
            meta = {
                "built_at": datetime.now(UK_TZ).isoformat(timespec="seconds"),
                "feed_timestamp": str(header.get("timestamp") or ""),
                "schedules": str(n_schedules),
                "public_schedules": str(n_public),
                "stops": str(_scalar(con, "SELECT count(*) FROM stops")),
                "valid_from": keep_from,
                "valid_to": str(_scalar(con, "SELECT max(end_date) FROM schedules WHERE public=1")),
            }
            with con.cursor() as cur:
                cur.executemany("INSERT INTO meta VALUES (%s, %s)", list(meta.items()))
            with con.transaction():
                con.execute("SET LOCAL lock_timeout = '60s'")
                con.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(live))
                con.execute(sql.SQL("ALTER SCHEMA {} RENAME TO {}").format(build_schema, live))
        finally:
            con.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(build_schema))
            con.execute(_UNLOCK_SQL, (db.schema,))
    Timetable.clear_caches()
    return meta


def _load(
    con: psycopg.Connection[Any],
    dsn: str,
    staging: str,
    lines: Iterable[str | bytes],
    keep_from: str,
) -> tuple[dict[str, Any], int, int]:
    """Stream the feed into the staging tables with COPY.

    Schedules and stops go through two connections so both COPYs can run while
    the feed is read once. TIPLOC records may appear anywhere in the feed.
    """
    tiplocs: dict[str, str] = {}
    header: dict[str, Any] = {}
    schedule_id = 0
    n_public = 0
    with (
        _connect(dsn, autocommit=True, search_path=staging) as stops_con,
        con.cursor().copy("COPY schedules FROM STDIN") as schedules_copy,
        stops_con.cursor().copy("COPY raw_stops FROM STDIN") as stops_copy,
    ):
        for rec in iter_records(lines):
            if "TiplocV1" in rec:
                t = rec["TiplocV1"]
                if t.get("crs_code") and t.get("tiploc_code"):
                    tiplocs[t["tiploc_code"]] = t["crs_code"]
            elif "JsonScheduleV1" in rec:
                parsed = parse_schedule(rec["JsonScheduleV1"])
                if not parsed:
                    continue
                row, stops = parsed
                if row["end_date"] < keep_from:
                    continue
                schedule_id += 1
                n_public += row["public"]
                schedules_copy.write_row((schedule_id, *row.values()))
                for i, st in enumerate(stops):
                    stops_copy.write_row((schedule_id, i, st.tiploc, st.arr, st.dep, st.platform))
            elif "JsonTimetableV1" in rec:
                header = rec["JsonTimetableV1"]
    with con.cursor().copy("COPY tiplocs FROM STDIN") as copy:
        for pair in tiplocs.items():
            copy.write_row(pair)
    return header, schedule_id, n_public


def _scalar(con: psycopg.Connection[Any], query: Query) -> Any:
    row = con.execute(query).fetchone()
    return row[0] if row else None


def _connect(
    dsn: str, *, autocommit: bool = False, search_path: str | None = None
) -> psycopg.Connection[Any]:
    if not dsn:
        raise TimetableMissing(
            "DATABASE_URL is not set. The timetable is stored in Postgres; set DATABASE_URL to "
            "a database this server can create schemas in."
        )
    options = f"-c search_path={search_path}" if search_path else ""
    try:
        return psycopg.connect(dsn, autocommit=autocommit, connect_timeout=10, options=options)
    except psycopg.OperationalError as exc:
        # The reason names the database's host and user: for the log, not the caller.
        log.warning("The timetable database can't be reached: %s", _first_line(exc))
        raise TimetableMissing("The timetable database can't be reached.") from exc


def _first_line(exc: Exception) -> str:
    return (str(exc).strip().splitlines() or [type(exc).__name__])[0]


def build_from_file(path: Path, db: TimetableDB) -> dict[str, Any]:
    """Build from a feed file, gzipped or not (detected from the content)."""
    with path.open("rb") as fh:
        gzipped = fh.read(2) == b"\x1f\x8b"
    if gzipped:
        with gzip.open(path, "rt", encoding="utf-8") as gz:
            return build(gz, db)
    with path.open(encoding="utf-8") as plain:
        return build(plain, db)


async def download_and_build(settings: Settings) -> dict[str, Any]:
    if not settings.has_nr:
        raise NotConfigured(
            "NR_USERNAME / NR_PASSWORD are not set. Register for Network Rail Open Data at "
            "https://publicdatafeeds.networkrail.co.uk/ntrod/welcome"
        )
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    fd, part = tempfile.mkstemp(dir=settings.data_dir, prefix="schedule.", suffix=".part")
    os.close(fd)
    gz_path = Path(part)
    auth = (settings.nr_username or "", settings.nr_password or "")
    try:
        async with (
            httpx.AsyncClient(follow_redirects=True, timeout=600) as http,
            http.stream("GET", settings.nr_schedule_url, auth=auth) as resp,
        ):
            if resp.status_code in (401, 403):
                raise UpstreamError("Network Rail rejected NR_USERNAME / NR_PASSWORD.")
            if not resp.is_success:
                raise UpstreamError(f"Network Rail download failed: HTTP {resp.status_code}")
            if "text/html" in resp.headers.get("content-type", ""):
                raise UpstreamError(
                    "Network Rail returned a web page instead of the feed; the login was probably "
                    "rejected, or NR_SCHEDULE_URL is wrong."
                )
            with gz_path.open("wb") as out:
                async for chunk in resp.aiter_bytes(1 << 20):
                    out.write(chunk)
        try:
            return await asyncio.to_thread(build_from_file, gz_path, settings.timetable_db)
        except (OSError, EOFError, UnicodeDecodeError) as exc:
            raise UpstreamError(f"The downloaded feed couldn't be read: {exc}") from exc
    finally:
        gz_path.unlink(missing_ok=True)


# ------------------------------------------------------------------- queries


@dataclass(frozen=True, slots=True)
class Stop:
    seq: int
    crs: str
    arr: int | None  # minutes after midnight of the service's run date
    dep: int | None
    platform: str | None


@dataclass(frozen=True, slots=True)
class Trip:
    """One schedule running on one date. Times are relative to `run_date`."""

    schedule_id: int
    uid: str
    run_date: date
    atoc: str | None
    headcode: str | None
    category: str | None
    stops: tuple[Stop, ...]

    @property
    def service_id(self) -> str:
        return f"tt:{self.uid}:{self.run_date.isoformat()}"

    @property
    def operator(self) -> str | None:
        return OPERATORS.get(self.atoc or "", self.atoc)

    @property
    def mode(self) -> str:
        if self.category in BUS_CATEGORIES:
            return "replacement bus" if self.category == "BR" else "bus"
        return "ferry" if self.category == "SS" else "train"


def runs_on(days_runs: str, start: str, end: str, day: date) -> bool:
    iso = day.isoformat()
    return start <= iso <= end and len(days_runs) == 7 and days_runs[day.weekday()] == "1"


class Timetable:
    """Read-only view over the timetable schema."""

    _instances: ClassVar[dict[TimetableDB, Timetable]] = {}

    def __init__(self, db: TimetableDB) -> None:
        self.db = db
        self._reader = _reader(db)
        self.meta = self._reader.meta()
        if not self.meta:
            raise TimetableMissing(
                "No timetable yet. It downloads automatically when NR_USERNAME/NR_PASSWORD are "
                "set, or run `traintracker refresh`."
            )

    @classmethod
    def open(cls, db: TimetableDB) -> Timetable:
        inst = cls._instances.get(db)
        if inst is None or inst._stale():
            inst = cls(db)
            cls._instances[db] = inst
        return inst

    def _stale(self) -> bool:
        return self._reader.meta().get("built_at") != self.meta.get("built_at")

    def query(self, query: Query, params: Any = ()) -> list[tuple[Any, ...]]:
        return self._reader.query(query, params)

    @classmethod
    def clear_caches(cls) -> None:
        """Forget loaded timetables and close their connections."""
        with _cache_lock:
            cls._instances.clear()
            _active_cache.clear()
            _day_trips.cache_clear()
        with _readers_lock:
            for reader in _readers.values():
                reader.close()
            _readers.clear()

    # -- which schedules run on a date --------------------------------------

    def active_ids(self, day: date) -> dict[int, str]:
        """schedule_id -> uid for public schedules running on `day`."""
        key = (self.db, self.meta.get("built_at"), day)
        if key in _active_cache:
            return _active_cache[key]
        iso = day.isoformat()
        best: dict[str, tuple[int, int, int]] = {}  # uid -> (priority, id, public)
        for sid, uid, stp, start, end, days, public in self.query(
            "SELECT id, uid, stp, start_date, end_date, days_runs, public FROM schedules "
            "WHERE start_date <= %s AND end_date >= %s",
            (iso, iso),
        ):
            if not runs_on(days, start, end, day):
                continue
            prio = STP_PRIORITY.get(stp, 9)
            if uid not in best or prio < best[uid][0]:
                best[uid] = (prio, sid, public)
        active = {sid: uid for uid, (prio, sid, public) in best.items() if prio != 0 and public}
        with _cache_lock:
            if len(_active_cache) > 6:
                _active_cache.pop(next(iter(_active_cache)), None)
            _active_cache[key] = active
        return active

    def _load_trips(self, ids: Iterable[int], run_date: date) -> list[Trip]:
        ids = list(ids)
        if not ids:
            return []
        meta: dict[int, tuple[Any, ...]] = {}
        stops: dict[int, list[Stop]] = {}
        for row in self.query(
            "SELECT id, uid, atoc, headcode, category FROM schedules WHERE id = ANY(%s)", (ids,)
        ):
            meta[row[0]] = row
        for sid, seq, crs, arr, dep, plat in self.query(
            "SELECT schedule_id, seq, crs, arr, dep, platform FROM stops "
            "WHERE schedule_id = ANY(%s) ORDER BY schedule_id, seq",
            (ids,),
        ):
            stops.setdefault(sid, []).append(Stop(seq, crs, arr, dep, plat))
        return [
            Trip(sid, m[1], run_date, m[2], m[3], m[4], tuple(stops.get(sid, ())))
            for sid, m in meta.items()
            if len(stops.get(sid, ())) >= 2
        ]

    def trips_at(
        self, crs: str, day: date, start_min: int, end_min: int, *, arriving: bool = False
    ) -> list[tuple[Trip, Stop]]:
        """Trips calling at `crs` with a public time in [start_min, end_min] of `day`.

        Minutes may exceed 1440 (next day). Trips that started the previous day
        are included, with their times shifted onto `day`.
        """
        col = sql.Identifier("arr" if arriving else "dep")
        out: list[tuple[Trip, Stop]] = []
        days = [(day, 0), (day - timedelta(days=1), DAY)]
        if end_min >= DAY:  # window runs past midnight into tomorrow's trains
            days.append((day + timedelta(days=1), -DAY))
        for run_date, shift in days:
            active = self.active_ids(run_date)
            lo, hi = start_min + shift, end_min + shift
            ids = [
                sid
                for (sid,) in self.query(
                    sql.SQL(
                        "SELECT schedule_id FROM stops WHERE crs = %s AND {} BETWEEN %s AND %s"
                    ).format(col),
                    (crs, lo, hi),
                )
                if sid in active
            ]
            for trip in self._load_trips(ids, run_date):
                for stop in trip.stops:
                    t = stop.arr if arriving else stop.dep
                    if stop.crs == crs and t is not None and lo <= t <= hi:
                        out.append((trip, stop))
                        break
        return out

    def day_trips(self, day: date) -> list[Trip]:
        """All public trips running on `day`, plus the previous day's late runners."""
        return _day_trips(self, self.meta.get("built_at"), day)

    def trip(self, uid: str, run_date: date) -> Trip | None:
        for sid, u in self.active_ids(run_date).items():
            if u == uid:
                trips = self._load_trips([sid], run_date)
                return trips[0] if trips else None
        return None


_active_cache: dict[tuple[Any, ...], dict[int, str]] = {}
_cache_lock = threading.Lock()


# Two days, as in planner._network: a day's trips are tens of megabytes.
@lru_cache(maxsize=2)
def _day_trips(tt: Timetable, _built_at: str | None, day: date) -> list[Trip]:
    trips = tt._load_trips(tt.active_ids(day).keys(), day)
    prev = day - timedelta(days=1)
    for trip in tt._load_trips(tt.active_ids(prev).keys(), prev):
        last = trip.stops[-1]
        if (last.arr or last.dep or 0) > DAY:  # still running after midnight
            trips.append(trip)
    return trips


class _Reader:
    """One autocommit connection per timetable schema, shared by all readers.

    The server calls in from its event loop and from worker threads, so access is
    serialised. A dropped connection (idle timeout, database restart) is reopened
    once before the error is raised.
    """

    def __init__(self, db: TimetableDB) -> None:
        self.db = db
        self._lock = threading.Lock()
        self._con: psycopg.Connection[Any] | None = None

    def query(self, query: Query, params: Any = ()) -> list[tuple[Any, ...]]:
        with self._lock:
            for attempt in (1, 2):
                if self._con is None or self._con.closed:
                    self._con = _connect(self.db.dsn, autocommit=True, search_path=self.db.schema)
                try:
                    return self._con.execute(query, params).fetchall()
                except psycopg.OperationalError:
                    self._con.close()
                    self._con = None
                    if attempt == 2:
                        raise
        raise AssertionError("unreachable")

    def close(self) -> None:
        with self._lock:
            if self._con is not None:
                self._con.close()
                self._con = None

    def meta(self) -> dict[str, str]:
        """The timetable's meta rows; empty if there is no timetable yet."""
        try:
            return dict(self.query("SELECT key, value FROM meta"))
        except psycopg.errors.UndefinedTable:
            return {}


_readers: dict[TimetableDB, _Reader] = {}
_readers_lock = threading.Lock()


def _reader(db: TimetableDB) -> _Reader:
    with _readers_lock:
        if db not in _readers:
            _readers[db] = _Reader(db)
        return _readers[db]


def minutes_on(day: date, trip: Trip, minutes: int | None) -> int | None:
    """Convert a trip time to minutes after midnight of `day`."""
    if minutes is None:
        return None
    return minutes + (trip.run_date - day).days * DAY


def to_datetime(day: date, minutes: int) -> datetime:
    """Wall-clock minutes after midnight of `day` -> aware UK datetime.

    Normalised through UTC so a time inside the spring-forward gap becomes a
    real time, and ambiguous autumn times get a consistent offset.
    """
    naive = datetime.combine(day, datetime.min.time()) + timedelta(minutes=minutes)
    return naive.replace(tzinfo=UK_TZ).astimezone(UTC).astimezone(UK_TZ)


def fmt_minutes(minutes: int | None) -> str | None:
    if minutes is None:
        return None
    m = minutes % DAY
    return f"{m // 60:02d}:{m % 60:02d}"
