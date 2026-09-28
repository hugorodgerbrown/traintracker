"""Demo mode: generated example data for testing before any account is approved.

Set TRAINTRACKER_DEMO=1. The server then:

- builds the demo timetable schema from a generated SCHEDULE feed (real stations
  and operators in East Anglia, invented times) covering the next 90 days, and
  rebuilds it each day;
- answers Darwin (LDBWS) requests in-process from that timetable, adding
  delays, cancellations and platform changes that are fixed per train and day.

Every tool therefore runs through its real code path (Darwin parsing and
paging, booked-platform fallback, live overlay in plan_journey), with no
network access and no credentials.

    CBG --(GN)--> RYS --> SVG --> FPK --> KGX  ~~tube~~  LST
    CBG --(LE)--> AUD --> BIS --> HWN --> TOM --> LST
    CBG --(LE)--> BSE --> SMK --> IPS
    LST --(LE)--> CHM --> COL --> IPS --> SMK --> DIS --> NRW        (intercity)
    LST --(LE)--> SRA --> SNF --> CHM --> WTM --> KEL --> MKT --> COL (stopping)
    MKT --(LE)--> CWC --> BUE --> SUY                                 (Sudbury branch)
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import unquote

import httpx

from traintracker import stations
from traintracker.config import UK_TZ, Settings
from traintracker.errors import TrainTrackerError
from traintracker.timetable import Stop, Timetable, Trip, build, fmt_minutes, minutes_on

DEMO_NOTE = (
    "Demo mode: these trains, times, delays and platforms are generated examples, "
    "not real services."
)
DAYS_AHEAD = 90
# Big stations announce live platforms only shortly before departure.
ANNOUNCE_MINUTES = 15
LARGE_STATIONS = {"LST", "KGX", "SRA", "CBG", "NRW", "IPS", "COL", "CHM"}

TIPLOCS = {
    "LIVST": "LST",
    "STFD": "SRA",
    "SHENFLD": "SNF",
    "CHLMSFD": "CHM",
    "WITHAME": "WTM",
    "KELVEDN": "KEL",
    "MRKSTEY": "MKT",
    "CLCHSTR": "COL",
    "IPSWICH": "IPS",
    "STWMRKT": "SMK",
    "DISS": "DIS",
    "NRCH": "NRW",
    "CHAPPEL": "CWC",
    "BURES": "BUE",
    "SUDBURY": "SUY",
    "CAMBDGE": "CBG",
    "ROYSTON": "RYS",
    "STEVNGE": "SVG",
    "FNPK": "FPK",
    "KNGX": "KGX",
    "AUDLYEN": "AUD",
    "BSHPSFD": "BIS",
    "HARLOWT": "HWN",
    "TTNHMHL": "TOM",
    "BSTEDMS": "BSE",
}

# (tiploc, minutes from origin, platforms heading out, platforms heading back).
# A platform tuple rotates by departure hour.
Plats = tuple[str, ...]
RouteStop = tuple[str, int, Plats, Plats]


@dataclass(frozen=True)
class Route:
    code: str  # first letter of each train UID
    atoc: str
    headcode: str  # class + area; the hour is appended
    stops: tuple[RouteStop, ...]
    out_minutes: tuple[int, ...]  # departure minutes past each hour from the first stop
    back_minutes: tuple[int, ...]  # the same from the last stop
    weekday_hours: range
    sunday_hours: range


ROUTES = (
    Route(
        "I",
        "LE",
        "1P",
        (
            ("LIVST", 0, ("11", "12"), ("11", "12")),
            ("CHLMSFD", 31, ("2",), ("1",)),
            ("CLCHSTR", 49, ("4",), ("3",)),
            ("IPSWICH", 65, ("2",), ("3",)),
            ("STWMRKT", 77, ("2",), ("1",)),
            ("DISS", 90, ("2",), ("1",)),
            ("NRCH", 112, ("3", "4", "5"), ("3", "4", "5")),
        ),
        (0,),
        (0,),
        range(6, 22),
        range(8, 21),
    ),
    Route(
        "S",
        "LE",
        "2C",
        (
            ("LIVST", 0, ("14", "15", "16"), ("14", "15", "16")),
            ("STFD", 7, ("9",), ("10",)),
            ("SHENFLD", 22, ("4",), ("1",)),
            ("CHLMSFD", 36, ("2",), ("1",)),
            ("WITHAME", 45, ("2",), ("1",)),
            ("KELVEDN", 50, ("1",), ("2",)),
            ("MRKSTEY", 56, ("1",), ("2",)),
            ("CLCHSTR", 63, ("4",), ("3",)),
        ),
        (30,),
        (30,),
        range(6, 23),
        range(8, 22),
    ),
    Route(
        "B",
        "LE",
        "2S",
        (
            ("MRKSTEY", 0, ("3",), ("3",)),
            ("CHAPPEL", 7, ("1",), ("1",)),
            ("BURES", 14, ("1",), ("1",)),
            ("SUDBURY", 20, ("1",), ("1",)),
        ),
        (35,),
        (5,),
        range(6, 23),
        range(9, 22),
    ),
    Route(
        "W",
        "LE",
        "2H",
        (
            ("LIVST", 0, ("3", "5", "7"), ("3", "5", "7")),
            ("TTNHMHL", 11, ("1",), ("2",)),
            ("HARLOWT", 29, ("1",), ("2",)),
            ("BSHPSFD", 38, ("1",), ("3",)),
            ("AUDLYEN", 51, ("1",), ("2",)),
            ("CAMBDGE", 72, ("4", "6"), ("1", "2")),
        ),
        (28, 58),
        (21, 51),
        range(6, 22),
        range(8, 21),
    ),
    Route(
        "G",
        "GN",
        "2T",
        (
            ("CAMBDGE", 0, ("7", "8"), ("7", "8")),
            ("ROYSTON", 14, ("1",), ("2",)),
            ("STEVNGE", 30, ("4",), ("1",)),
            ("FNPK", 47, ("4",), ("3",)),
            ("KNGX", 53, ("9", "10", "11"), ("9", "10", "11")),
        ),
        (15, 45),
        (12, 42),
        range(6, 23),
        range(7, 22),
    ),
    Route(
        "E",
        "LE",
        "2W",
        (
            ("CAMBDGE", 0, ("2",), ("2",)),
            ("BSTEDMS", 39, ("1",), ("2",)),
            ("STWMRKT", 56, ("2",), ("1",)),
            ("IPSWICH", 71, ("3",), ("4",)),
        ),
        (44,),
        (5,),
        range(6, 22),
        range(8, 21),
    ),
)

WEEKDAYS, SUNDAYS = "1111110", "0000001"


# ------------------------------------------------------------------ timetable


def _hhmm(minutes: int) -> str:
    minutes %= 24 * 60
    return f"{minutes // 60:02d}{minutes % 60:02d}"


def _location(tiploc: str, arr: int | None, dep: int | None, platform: str) -> dict[str, Any]:
    kind = "LO" if arr is None else "LT" if dep is None else "LI"
    return {
        "location_type": kind,
        "record_identity": kind,
        "tiploc_code": tiploc,
        "arrival": _hhmm(arr) if arr is not None else None,
        "departure": _hhmm(dep) if dep is not None else None,
        "public_arrival": _hhmm(arr) if arr is not None else None,
        "public_departure": _hhmm(dep) if dep is not None else None,
        "platform": platform,
    }


def _journey(route: Route, back: bool, start: int) -> list[dict[str, Any]]:
    """One train's locations. Intermediate stops dwell for a minute."""
    total = route.stops[-1][1]
    stops = (
        [(t, total - m, back_p) for t, m, _, back_p in reversed(route.stops)]
        if back
        else [(t, m, out_p) for t, m, out_p, _ in route.stops]
    )
    hour = start // 60
    out = []
    for i, (tiploc, offset, plats) in enumerate(stops):
        t = start + offset
        platform = plats[hour % len(plats)]
        first, last = i == 0, i == len(stops) - 1
        out.append(
            _location(
                tiploc, None if first else t, None if last else t + (0 if first else 1), platform
            )
        )
    return out


def feed(today: date) -> list[str]:
    """A SCHEDULE feed (newline-delimited JSON) running from two days ago for DAYS_AHEAD."""
    start = (today - timedelta(days=2)).isoformat()
    end = (today + timedelta(days=DAYS_AHEAD)).isoformat()
    records: list[dict[str, Any]] = [
        {"JsonTimetableV1": {"timestamp": 0, "Metadata": {"type": "full", "demo": True}}}
    ]
    for tiploc, crs in TIPLOCS.items():
        records.append(
            {"TiplocV1": {"transaction_type": "Create", "tiploc_code": tiploc, "crs_code": crs}}
        )
    for route in ROUTES:
        for days, hours, sunday in (
            (WEEKDAYS, route.weekday_hours, False),
            (SUNDAYS, route.sunday_hours, True),
        ):
            for back, minutes in ((False, route.out_minutes), (True, route.back_minutes)):
                for h in hours:
                    for m in minutes:
                        dep = h * 60 + m
                        code = route.code.lower() if sunday else route.code
                        uid = f"{code}{'U' if back else 'D'}{h:02d}{m:02d}"
                        records.append(
                            {
                                "JsonScheduleV1": {
                                    "CIF_train_uid": uid,
                                    "CIF_stp_indicator": "P",
                                    "transaction_type": "Create",
                                    "schedule_start_date": start,
                                    "schedule_end_date": end,
                                    "schedule_days_runs": days,
                                    "atoc_code": route.atoc,
                                    "train_status": "P",
                                    "schedule_segment": {
                                        "signalling_id": f"{route.headcode}{h:02d}",
                                        "CIF_train_category": "OO",
                                        "schedule_location": _journey(route, back, dep),
                                    },
                                }
                            }
                        )
    return [json.dumps(r) for r in records]


def is_current(settings: Settings) -> bool:
    """True if the demo timetable exists and was built today."""
    try:
        built = Timetable.open(settings.timetable_db).meta.get("built_at")
    except TrainTrackerError:
        return False
    if not built:
        return False
    return datetime.fromisoformat(built).date() == datetime.now(UK_TZ).date()


def build_timetable(settings: Settings) -> dict[str, Any]:
    today = datetime.now(UK_TZ).date()
    return build(feed(today), settings.timetable_db, today=today)


def ensure_timetable(settings: Settings) -> None:
    if not is_current(settings):
        build_timetable(settings)


# --------------------------------------------------------------------- Darwin


DELAY_REASONS = (
    "This train has been delayed by a signalling problem",
    "This train has been delayed by a late running train in front of this one",
    "This train has been delayed by a fault on this train",
    "This train has been delayed by more passengers than usual",
)
CANCEL_REASONS = (
    "This train has been cancelled because of a shortage of train crew",
    "This train has been cancelled because of a fault on this train",
)


@dataclass(frozen=True)
class Running:
    """How one train runs on one day. Fixed by UID and date, so repeat calls agree."""

    cancelled: bool
    delay: int | None  # minutes; None = "Delayed" with no estimate
    reason: str | None
    platform_change: bool


def running(trip: Trip) -> Running:
    h = int(hashlib.sha256(f"{trip.uid}:{trip.run_date}".encode()).hexdigest(), 16)
    roll = h % 100
    pick = (h >> 8) % 1000
    change = (h >> 20) % 100 < 8
    if roll < 4:
        return Running(True, 0, CANCEL_REASONS[pick % len(CANCEL_REASONS)], change)
    if roll < 6:
        return Running(False, None, DELAY_REASONS[pick % len(DELAY_REASONS)], change)
    if roll < 28:
        delay = 2 + pick % 19
        reason = DELAY_REASONS[pick % len(DELAY_REASONS)] if delay >= 5 else None
        return Running(False, delay, reason, change)
    return Running(False, 0, None, change)


def _name(crs: str) -> str:
    s = stations.by_crs(crs)
    return s.name if s else crs


def _ref(crs: str) -> list[dict[str, str]]:
    return [{"locationName": _name(crs), "crs": crs}]


def _service_id(trip: Trip, crs: str) -> str:
    return f"{trip.uid}-{trip.run_date:%Y%m%d}-{crs}"


class DemoDarwin:
    """An httpx handler answering LDBWS board and service requests from the demo timetable."""

    def __init__(self, settings: Settings, clock: Callable[[], datetime]) -> None:
        self.settings = settings
        self.clock = clock

    def __call__(self, request: httpx.Request) -> httpx.Response:
        *_, op, arg = request.url.path.rstrip("/").split("/")
        arg = unquote(arg)
        tt = Timetable.open(self.settings.timetable_db)
        now = self.clock().astimezone(UK_TZ)
        params = request.url.params
        if op in ("GetDepBoardWithDetails", "GetArrBoardWithDetails"):
            body = self._board(tt, now, arg.upper(), op == "GetArrBoardWithDetails", params)
            return httpx.Response(200, json=body)
        if op == "GetServiceDetails":
            detail = self._detail(tt, now, arg)
            return httpx.Response(200, json=detail) if detail else httpx.Response(200)
        return httpx.Response(404)

    # -- times ----------------------------------------------------------------

    def _now_minutes(self, now: datetime, day: date) -> int:
        return (now.date() - day).days * 24 * 60 + now.hour * 60 + now.minute

    def _expected(self, run: Running, minutes: int | None) -> str | None:
        """Darwin's et/etd text for a stop time (trip-relative minutes)."""
        if minutes is None:
            return None
        if run.cancelled:
            return "Cancelled"
        if run.delay is None:
            return "Delayed"
        return "On time" if run.delay == 0 else fmt_minutes(minutes + run.delay)

    def _actual(self, now: datetime, trip: Trip, run: Running, minutes: int | None) -> str | None:
        """Darwin's at text once a stop has been passed, else None."""
        if minutes is None or run.cancelled or run.delay is None:
            return None
        if minutes + run.delay > self._now_minutes(now, trip.run_date):
            return None
        return "On time" if run.delay == 0 else fmt_minutes(minutes + run.delay)

    def _platform(self, now: datetime, trip: Trip, run: Running, stop: Stop) -> str | None:
        if run.cancelled or not stop.platform:
            return None
        t = stop.dep if stop.dep is not None else stop.arr
        if (
            stop.crs in LARGE_STATIONS
            and t is not None
            and t - self._now_minutes(now, trip.run_date) > ANNOUNCE_MINUTES
        ):
            return None  # not announced yet
        if run.platform_change and stop.platform.isdigit():
            return str(int(stop.platform) + 1)
        return stop.platform

    def _points(
        self, now: datetime, trip: Trip, run: Running, stops: tuple[Stop, ...], previous: bool
    ) -> list[dict[str, Any]]:
        points = []
        for s in stops:
            t = (s.dep if s.dep is not None else s.arr) if previous else (s.arr or s.dep)
            cp: dict[str, Any] = {
                "locationName": _name(s.crs),
                "crs": s.crs,
                "st": fmt_minutes(t),
                "et": None,
                "at": None,
                "isCancelled": run.cancelled,
            }
            actual = self._actual(now, trip, run, t)
            if actual:
                cp["at"] = actual
            else:
                cp["et"] = self._expected(run, t)
            points.append(cp)
        return [{"callingPoint": points}] if points else []

    # -- responses ------------------------------------------------------------

    def _board(
        self,
        tt: Timetable,
        now: datetime,
        crs: str,
        arriving: bool,
        params: httpx.QueryParams,
    ) -> dict[str, Any]:
        rows = int(params.get("numRows", 10))
        offset = int(params.get("timeOffset", 0))
        window = int(params.get("timeWindow", 120))
        filter_crs = (params.get("filterCrs") or "").upper() or None
        day = now.date()
        start = now.hour * 60 + now.minute + offset
        end = start + window
        found: list[tuple[int, dict[str, Any]]] = []
        for trip, stop in tt.trips_at(crs, day, start - 30, end, arriving=arriving):
            idx = trip.stops.index(stop)
            before, after = trip.stops[:idx], trip.stops[idx + 1 :]
            if filter_crs and not any(s.crs == filter_crs for s in (before if arriving else after)):
                continue
            run = running(trip)
            t = minutes_on(day, trip, stop.arr if arriving else stop.dep)
            assert t is not None
            gone = run.cancelled or run.delay is None or t + (run.delay or 0) < start
            if t < start and gone:
                continue  # already left
            sched = fmt_minutes(t)
            exp = self._expected(run, stop.arr if arriving else stop.dep)
            svc: dict[str, Any] = {
                "sta" if arriving else "std": sched,
                "eta" if arriving else "etd": exp,
                "platform": self._platform(now, trip, run, stop),
                "operator": trip.operator,
                "operatorCode": trip.atoc,
                "serviceType": "train",
                "serviceID": _service_id(trip, crs),
                "isCancelled": run.cancelled,
                "cancelReason": run.reason if run.cancelled else None,
                "delayReason": None if run.cancelled else run.reason,
                "origin": _ref(trip.stops[0].crs),
                "destination": _ref(trip.stops[-1].crs),
            }
            if arriving:
                svc["previousCallingPoints"] = self._points(now, trip, run, before, True)
            else:
                svc["subsequentCallingPoints"] = self._points(now, trip, run, after, False)
            found.append((t, svc))
        found.sort(key=lambda x: x[0])
        return {
            "generatedAt": now.isoformat(),
            "locationName": _name(crs),
            "crs": crs,
            "filterLocationName": _name(filter_crs) if filter_crs else None,
            "filtercrs": filter_crs,
            "platformAvailable": True,
            "nrccMessages": [{"Value": DEMO_NOTE}],
            "trainServices": [svc for _, svc in found[:rows]],
        }

    def _detail(self, tt: Timetable, now: datetime, service_id: str) -> dict[str, Any] | None:
        try:
            uid, run_day, crs = service_id.split("-")
            run_date = datetime.strptime(run_day, "%Y%m%d").date()
        except ValueError:
            return None
        trip = tt.trip(uid, run_date)
        here = next((i for i, s in enumerate(trip.stops) if s.crs == crs), None) if trip else None
        if trip is None or here is None:
            return None
        run = running(trip)
        stop = trip.stops[here]
        return {
            "generatedAt": now.isoformat(),
            "locationName": _name(crs),
            "crs": crs,
            "operator": trip.operator,
            "operatorCode": trip.atoc,
            "serviceType": "train",
            "isCancelled": run.cancelled,
            "cancelReason": run.reason if run.cancelled else None,
            "delayReason": None if run.cancelled else run.reason,
            "platform": self._platform(now, trip, run, stop),
            "sta": fmt_minutes(stop.arr),
            "eta": self._expected(run, stop.arr),
            "ata": self._actual(now, trip, run, stop.arr),
            "std": fmt_minutes(stop.dep),
            "etd": self._expected(run, stop.dep),
            "atd": self._actual(now, trip, run, stop.dep),
            "previousCallingPoints": self._points(now, trip, run, trip.stops[:here], True),
            "subsequentCallingPoints": self._points(now, trip, run, trip.stops[here + 1 :], False),
        }


def transport(
    settings: Settings, clock: Callable[[], datetime] = lambda: datetime.now(UK_TZ)
) -> httpx.MockTransport:
    return httpx.MockTransport(DemoDarwin(settings, clock))
