"""Journey planning over the timetable using the Connection Scan Algorithm.

CSA scans every train "hop" of the day in departure order, keeping the earliest
time each station can be reached. It finds earliest-arrival journeys with any
number of changes in well under a second for the whole GB network.

Cross-London transfers between terminals are modelled as approximate
walk/Tube links, since the Tube isn't in the Network Rail timetable.
"""

from __future__ import annotations

import bisect
import math
import threading
from dataclasses import dataclass, replace
from datetime import date, datetime
from functools import lru_cache
from itertools import pairwise
from typing import Literal

from traintracker import stations
from traintracker.config import UK_TZ
from traintracker.models import Journey, JourneyLeg, StationRef
from traintracker.timetable import Timetable, Trip, minutes_on, to_datetime

INF = 10**9
SEARCH_HORIZON = 12 * 60  # stop scanning 12h after the requested time

# Terminals and interchanges linked by walking or the Tube/Elizabeth line.
LONDON_LINKS = [
    "EUS", "KGX", "STP", "PAD", "MYB", "LST", "FST", "CST", "CHX",
    "WAT", "WAE", "VIC", "LBG", "BFR", "CTK", "MOG", "ZFD",
]  # fmt: skip


@dataclass(frozen=True, slots=True)
class Footpath:
    to: str
    minutes: int
    mode: Literal["walk", "tube"]


def _km(a: stations.Station, b: stations.Station) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a.lat, a.lon, b.lat, b.lon))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 6371 * 2 * math.asin(math.sqrt(h))


@lru_cache(maxsize=1)
def footpaths() -> dict[str, tuple[Footpath, ...]]:
    """Approximate door-to-platform transfer times between London stations.

    Under 0.8 km: walk at ~4 km/h plus 5 min to get in and out of stations.
    Further: Tube, ~12 min access/wait plus ~3.5 min per km. Deliberately
    cautious; the person should check live Tube status.
    """
    known = [s for c in LONDON_LINKS if (s := stations.by_crs(c))]
    out: dict[str, list[Footpath]] = {s.crs: [] for s in known}
    for a in known:
        for b in known:
            if a.crs == b.crs:
                continue
            km = _km(a, b)
            if km < 0.8:
                out[a.crs].append(Footpath(b.crs, math.ceil(5 + km * 15), "walk"))
            else:
                out[a.crs].append(Footpath(b.crs, math.ceil(12 + km * 3.5), "tube"))
    return {k: tuple(v) for k, v in out.items()}


@dataclass(frozen=True, slots=True)
class Conn:
    dep: int
    arr: int
    frm: str
    to: str
    trip: int
    i: int  # index of the departure stop within the trip
    can_board: bool
    can_alight: bool


@dataclass(frozen=True, slots=True)
class Network:
    day: date
    trips: tuple[Trip, ...]
    conns: tuple[Conn, ...]
    deps: tuple[int, ...]  # conns' departure times, for bisect


_network_lock = threading.Lock()


def network(tt: Timetable, built_at: str | None, day: date) -> Network:
    """The day's connections, built once and shared (prewarm and tools race)."""
    with _network_lock:
        return _network(tt, built_at, day)


# A day's network is over a hundred megabytes, and the hosted server has 512 MB:
# today and one other date are kept, and a third replaces the older of them.
@lru_cache(maxsize=2)
def _network(tt: Timetable, _built_at: str | None, day: date) -> Network:
    trips = tuple(tt.day_trips(day))
    conns: list[Conn] = []
    for ti, trip in enumerate(trips):
        st = trip.stops
        for i in range(len(st) - 1):
            a, b = st[i], st[i + 1]
            leave = minutes_on(day, trip, a.dep if a.dep is not None else a.arr)
            reach = minutes_on(day, trip, b.arr if b.arr is not None else b.dep)
            if leave is None or reach is None:
                continue
            conns.append(
                Conn(leave, reach, a.crs, b.crs, ti, i, a.dep is not None, b.arr is not None)
            )
    conns.sort(key=lambda c: (c.dep, c.arr))
    return Network(day, trips, tuple(conns), tuple(c.dep for c in conns))


# Pointers used to rebuild a journey backwards from the destination.
@dataclass(frozen=True, slots=True)
class _Ride:
    trip: int
    board: int  # conn index
    alight: int  # conn index


@dataclass(frozen=True, slots=True)
class _Walk:
    frm: str
    minutes: int
    mode: str


@dataclass(frozen=True, slots=True)
class RawLeg:
    kind: Literal["ride", "walk"]
    frm: str
    to: str
    dep: int
    arr: int
    trip: Trip | None = None
    board_i: int = 0
    alight_i: int = 0
    mode: str = "walk"


Pointer = tuple[int, "_Ride | _Walk"]  # (round it was set in, how we got here)


def pareto_journeys(
    net: Network, origin: str, dest: str, t0: int, mct: int, max_rides: int
) -> list[list[RawLeg]]:
    """Round-based Connection Scan.

    Round r allows r rides, boarding only from stations reached in round r-1.
    Returns one journey per round that improves the arrival time, so the result
    runs from fewest changes (slowest) to most changes (fastest).
    """
    fp = footpaths()
    ready: dict[str, int] = {origin: t0}
    ptr: dict[str, Pointer] = {}
    for f in fp.get(origin, ()):
        ready[f.to] = t0 + f.minutes
        ptr[f.to] = (0, _Walk(origin, f.minutes, f.mode))
    readies, ptrs = [ready], [ptr]
    best = ready.get(dest, INF) if dest != origin else INF
    results = [_rebuild(net, ptrs, dest, origin, 0, best)] if best < INF else []
    conns = net.conns
    start = bisect.bisect_left(net.deps, t0)
    limit = t0 + SEARCH_HORIZON

    for r in range(1, max_rides + 1):
        prev = readies[-1]
        cur = dict(prev)
        ptr = dict(ptrs[-1])
        round_best = best
        improved = False
        boarded: dict[int, int] = {}
        for ci in range(start, len(conns)):
            c = conns[ci]
            if c.dep >= round_best or c.dep > limit:
                break
            if c.trip not in boarded:
                if not (c.can_board and prev.get(c.frm, INF) <= c.dep):
                    continue
                boarded[c.trip] = ci
            if not c.can_alight:
                continue
            ride = _Ride(c.trip, boarded[c.trip], ci)
            if c.to == dest:
                if c.arr < round_best:
                    round_best = c.arr
                    ptr[dest] = (r, ride)
                continue
            if c.arr + mct < cur.get(c.to, INF):
                cur[c.to] = c.arr + mct
                ptr[c.to] = (r, ride)
                improved = True
                for f in fp.get(c.to, ()):
                    t = c.arr + f.minutes
                    if f.to == dest:
                        if t < round_best:
                            round_best = t
                            ptr[dest] = (r, _Walk(c.to, f.minutes, f.mode))
                    elif t < cur.get(f.to, INF):
                        cur[f.to] = t
                        ptr[f.to] = (r, _Walk(c.to, f.minutes, f.mode))
        readies.append(cur)
        ptrs.append(ptr)
        if round_best < best:
            best = round_best
            results.append(_rebuild(net, ptrs, dest, origin, r, best))
        if not improved:
            break  # nothing new reachable; more rounds can't help
    return results


def _rebuild(
    net: Network, ptrs: list[dict[str, Pointer]], dest: str, origin: str, r: int, arrive: int
) -> list[RawLeg]:
    legs: list[RawLeg] = []
    station, rnd, arrive_at = dest, r, arrive
    for _ in range(100):  # bounded: at most one ride and one walk per round
        if station == origin:
            break
        set_in, p = ptrs[rnd][station]
        if isinstance(p, _Ride):
            b, a = net.conns[p.board], net.conns[p.alight]
            legs.append(RawLeg("ride", b.frm, a.to, b.dep, a.arr, net.trips[p.trip], b.i, a.i + 1))
            station, rnd, arrive_at = b.frm, set_in - 1, b.dep
        else:
            dep = arrive_at - p.minutes
            legs.append(RawLeg("walk", p.frm, station, dep, arrive_at, mode=p.mode))
            station, rnd, arrive_at = p.frm, set_in, dep
    legs.reverse()
    return legs


# ----------------------------------------------------------------- formatting


def _ref(crs: str) -> StationRef:
    s = stations.by_crs(crs)
    return StationRef(name=s.name if s else crs, crs=crs)


def _iso(day: date, minutes: int) -> str:
    return to_datetime(day, minutes).isoformat(timespec="minutes")


def to_journey(day: date, raw: list[RawLeg], t0: int) -> Journey:
    legs: list[JourneyLeg] = []
    for n, leg in enumerate(raw):
        if leg.kind == "walk":
            if n > 0:
                # Transfers start when the previous train arrives, not just
                # before the next one leaves.
                minutes = leg.arr - leg.dep
                leg = replace(leg, dep=raw[n - 1].arr, arr=raw[n - 1].arr + minutes)
            legs.append(
                JourneyLeg(
                    service_id="",
                    mode="walk" if leg.mode == "walk" else "tube (approx.)",
                    board_at=_ref(leg.frm),
                    alight_at=_ref(leg.to),
                    destination=_ref(leg.to).name,
                    depart_scheduled=_iso(day, leg.dep),
                    arrive_scheduled=_iso(day, leg.arr),
                )
            )
            continue
        trip = leg.trip
        assert trip is not None
        board = trip.stops[leg.board_i]
        ride = JourneyLeg(
            service_id=trip.service_id,
            operator=trip.operator,
            mode=trip.mode,
            board_at=_ref(leg.frm),
            alight_at=_ref(leg.to),
            destination=_ref(trip.stops[-1].crs).name,
            depart_scheduled=_iso(day, leg.dep),
            arrive_scheduled=_iso(day, leg.arr),
            platform=board.platform,
        )
        ride._calls = tuple(s.crs for s in trip.stops)
        legs.append(ride)
    # Leading walk: leave just in time for the first ride (walk/Tube times
    # already include getting in and out of stations), but never before t0.
    if len(raw) > 1 and raw[0].kind == "walk":
        walk = raw[0].arr - raw[0].dep
        leave = max(t0, raw[1].dep - walk)
        legs[0].depart_scheduled = _iso(day, leave)
        legs[0].arrive_scheduled = _iso(day, leave + walk)
    depart = datetime.fromisoformat(legs[0].depart_scheduled)
    arrive = datetime.fromisoformat(legs[-1].arrive_scheduled)
    rides = [leg for leg in legs if leg.service_id]
    gaps = [
        round(
            (
                datetime.fromisoformat(nxt.depart_scheduled)
                - datetime.fromisoformat(cur.arrive_scheduled)
            ).total_seconds()
            / 60
        )
        for cur, nxt in pairwise(legs)
    ]
    return Journey(
        legs=legs,
        depart=legs[0].depart_scheduled,
        arrive=legs[-1].arrive_scheduled,
        duration_minutes=round((arrive - depart).total_seconds() / 60),
        changes=max(0, len(rides) - 1),
        interchange_minutes=gaps,
    )


def plan(
    tt: Timetable,
    origin: str,
    dest: str,
    when: datetime,
    *,
    via: str | None = None,
    count: int = 3,
    max_changes: int = 4,
    mct: int = 5,
) -> list[Journey]:
    """Up to `count` journeys departing at or after `when`.

    Ordered by arrival. Includes the fewest-changes option even when it's
    slower, and never an option that another beats on departure, arrival and
    changes at once.
    """
    local = when.astimezone(UK_TZ)
    day = local.date()
    net = network(tt, tt.meta.get("built_at"), day)
    t0 = t = local.hour * 60 + local.minute
    max_rides = max_changes + 1
    found: dict[tuple[str, str, int], Journey] = {}
    for _ in range(count * 6):
        options = _search(net, origin, dest, t, via, mct, max_rides)
        if not options:
            break
        for raw in options:
            j = to_journey(day, raw, t0)
            if j.changes <= max_changes:
                found.setdefault((j.depart, j.arrive, j.changes), j)
        rides = [leg.dep for raw in options for leg in raw if leg.kind == "ride"]
        if not rides:  # walk-only: nothing later to find
            break
        t = min(rides) + 1
        if len({j.arrive for j in found.values()}) >= count + 2:
            break

    journeys = [
        j for j in found.values()
        if not any(_dominates(k, j) for k in found.values() if k is not j)
    ]  # fmt: skip
    journeys.sort(key=lambda j: (j.arrive, j.changes, j.depart))
    chosen = journeys[:count]
    fewest = min(journeys, key=lambda j: (j.changes, j.arrive), default=None)
    if (
        fewest
        and fewest not in chosen
        and chosen
        and fewest.changes < min(j.changes for j in chosen)
    ):
        chosen[-1] = fewest
    return chosen


def _dominates(a: Journey, b: Journey) -> bool:
    """a is at least as good as b on every count and better on one."""
    no_worse = a.depart >= b.depart and a.arrive <= b.arrive and a.changes <= b.changes
    better = a.depart > b.depart or a.arrive < b.arrive or a.changes < b.changes
    return no_worse and better


def _search(
    net: Network, origin: str, dest: str, t: int, via: str | None, mct: int, max_rides: int
) -> list[list[RawLeg]]:
    if not via:
        return pareto_journeys(net, origin, dest, t, mct, max_rides)
    first = pareto_journeys(net, origin, via, t, mct, max_rides)
    if not first:
        return []
    head = first[-1]  # fastest to the via station
    tails = pareto_journeys(net, via, dest, head[-1].arr + mct, mct, max_rides)
    return [head + tail for tail in tails]
