"""Network Rail SCHEDULE feed -> local SQLite timetable, and queries over it.

The feed (CIF_ALL_FULL_DAILY, JSON) is newline-delimited JSON with records:
  {"JsonTimetableV1": {...}}  header
  {"TiplocV1": {...}}         location reference: tiploc_code -> crs_code
  {"JsonAssociationV1": ...}  joins/divides (ignored)
  {"JsonScheduleV1": {...}}   one schedule record per UID/date-range/STP

A train (CIF_train_uid) can have several overlapping records. For a given day
the one that applies is picked by STP indicator: C (cancelled) > N > O > P.
We keep public passenger stops only (those with a public time), which keeps
the database small (roughly 100-200 MB) and every query fast.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
import os
import sqlite3
import tempfile
import threading
from collections.abc import Iterable, Iterator
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, ClassVar

import httpx

from traintracker.config import UK_TZ, Settings
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

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE schedules (
    id INTEGER PRIMARY KEY,
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
CREATE TABLE stops (
    schedule_id INTEGER NOT NULL,
    seq INTEGER NOT NULL,
    crs TEXT NOT NULL,
    arr INTEGER,
    dep INTEGER,
    platform TEXT,
    PRIMARY KEY (schedule_id, seq)
) WITHOUT ROWID;
"""

INDEXES = """
CREATE INDEX schedules_uid ON schedules (uid);
CREATE INDEX schedules_dates ON schedules (start_date, end_date);
CREATE INDEX stops_crs ON stops (crs, dep);
"""


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


def build(lines: Iterable[str | bytes], db_path: Path, today: date | None = None) -> dict[str, Any]:
    """Build a fresh timetable DB from feed lines; atomically replace db_path."""
    today = today or datetime.now(UK_TZ).date()
    keep_from = (today - timedelta(days=2)).isoformat()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=db_path.parent, suffix=".sqlite.tmp")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        con = sqlite3.connect(tmp)
        con.executescript("PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;" + SCHEMA)
        con.execute("CREATE TEMP TABLE raw_stops (schedule_id, seq, tiploc, arr, dep, platform)")
        tiplocs: dict[str, str] = {}
        header: dict[str, Any] = {}
        schedule_id = 0
        n_public = 0
        batch: list[tuple[Any, ...]] = []
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
                con.execute(
                    "INSERT INTO schedules VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (schedule_id, *row.values()),
                )
                batch.extend(
                    (schedule_id, i, s.tiploc, s.arr, s.dep, s.platform)
                    for i, s in enumerate(stops)
                )
                if len(batch) > 50_000:
                    con.executemany("INSERT INTO raw_stops VALUES (?,?,?,?,?,?)", batch)
                    batch.clear()
            elif "JsonTimetableV1" in rec:
                header = rec["JsonTimetableV1"]
        con.executemany("INSERT INTO raw_stops VALUES (?,?,?,?,?,?)", batch)

        # Resolve TIPLOC -> CRS; stops at locations without a CRS are dropped.
        con.execute("CREATE TEMP TABLE tiplocs (tiploc TEXT PRIMARY KEY, crs TEXT)")
        con.executemany("INSERT INTO tiplocs VALUES (?,?)", tiplocs.items())
        con.execute(
            """INSERT INTO stops
               SELECT r.schedule_id, r.seq, t.crs, r.arr, r.dep, r.platform
               FROM raw_stops r JOIN tiplocs t ON t.tiploc = r.tiploc"""
        )
        con.executescript(INDEXES)
        meta = {
            "built_at": datetime.now(UK_TZ).isoformat(timespec="seconds"),
            "feed_timestamp": str(header.get("timestamp") or ""),
            "schedules": str(schedule_id),
            "public_schedules": str(n_public),
            "stops": str(con.execute("SELECT count(*) FROM stops").fetchone()[0]),
            "valid_to": str(
                con.execute("SELECT max(end_date) FROM schedules WHERE public=1").fetchone()[0]
            ),
        }
        con.executemany("INSERT INTO meta VALUES (?,?)", meta.items())
        con.commit()
        con.execute("VACUUM")
        con.close()
        if n_public == 0:
            raise TrainTrackerError("The feed contained no passenger schedules; keeping old DB.")
        os.replace(tmp, db_path)
        Timetable.clear_caches()
        return meta
    finally:
        tmp.unlink(missing_ok=True)


def build_from_file(path: Path, db_path: Path) -> dict[str, Any]:
    """Build from a feed file, gzipped or not (detected from the content)."""
    with path.open("rb") as fh:
        gzipped = fh.read(2) == b"\x1f\x8b"
    if gzipped:
        with gzip.open(path, "rt", encoding="utf-8") as gz:
            return build(gz, db_path)
    with path.open(encoding="utf-8") as plain:
        return build(plain, db_path)


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
            return await asyncio.to_thread(build_from_file, gz_path, settings.timetable_path)
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
    """Read-only view over the SQLite timetable."""

    _instances: ClassVar[dict[Path, Timetable]] = {}

    def __init__(self, path: Path) -> None:
        if not path.exists():
            raise TimetableMissing(
                "No timetable yet. It downloads automatically when NR_USERNAME/NR_PASSWORD are "
                "set, or run `traintracker refresh`."
            )
        self.path = path
        self.con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
        self.meta = dict(self.con.execute("SELECT key, value FROM meta").fetchall())

    @classmethod
    def open(cls, path: Path) -> Timetable:
        inst = cls._instances.get(path)
        if inst is None or not path.exists() or inst._stale():
            inst = cls(path)
            cls._instances[path] = inst
        return inst

    def _stale(self) -> bool:
        try:
            with closing(sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)) as con:
                row = con.execute("SELECT value FROM meta WHERE key='built_at'").fetchone()
        except sqlite3.Error:
            return True
        return bool(row and row[0] != self.meta.get("built_at"))

    @classmethod
    def clear_caches(cls) -> None:
        with _cache_lock:
            cls._instances.clear()
            _active_cache.clear()

    # -- which schedules run on a date --------------------------------------

    def active_ids(self, day: date) -> dict[int, str]:
        """schedule_id -> uid for public schedules running on `day`."""
        key = (self.path, self.meta.get("built_at"), day)
        if key in _active_cache:
            return _active_cache[key]
        iso = day.isoformat()
        best: dict[str, tuple[int, int, int]] = {}  # uid -> (priority, id, public)
        for sid, uid, stp, start, end, days, public in self.con.execute(
            "SELECT id, uid, stp, start_date, end_date, days_runs, public FROM schedules "
            "WHERE start_date <= ? AND end_date >= ?",
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
        for chunk in _chunks(ids, 900):
            marks = ",".join("?" * len(chunk))
            for row in self.con.execute(
                f"SELECT id, uid, atoc, headcode, category FROM schedules WHERE id IN ({marks})",
                chunk,
            ):
                meta[row[0]] = row
            for sid, seq, crs, arr, dep, plat in self.con.execute(
                f"SELECT schedule_id, seq, crs, arr, dep, platform FROM stops "
                f"WHERE schedule_id IN ({marks}) ORDER BY schedule_id, seq",
                chunk,
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
        col = "arr" if arriving else "dep"
        out: list[tuple[Trip, Stop]] = []
        days = [(day, 0), (day - timedelta(days=1), DAY)]
        if end_min >= DAY:  # window runs past midnight into tomorrow's trains
            days.append((day + timedelta(days=1), -DAY))
        for run_date, shift in days:
            active = self.active_ids(run_date)
            lo, hi = start_min + shift, end_min + shift
            ids = [
                sid
                for (sid,) in self.con.execute(
                    f"SELECT schedule_id FROM stops WHERE crs = ? AND {col} BETWEEN ? AND ?",
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


@lru_cache(maxsize=3)
def _day_trips(tt: Timetable, _built_at: str | None, day: date) -> list[Trip]:
    trips = tt._load_trips(tt.active_ids(day).keys(), day)
    prev = day - timedelta(days=1)
    for trip in tt._load_trips(tt.active_ids(prev).keys(), prev):
        last = trip.stops[-1]
        if (last.arr or last.dep or 0) > DAY:  # still running after midnight
            trips.append(trip)
    return trips


def _chunks(items: list[int], size: int) -> Iterator[list[int]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


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
