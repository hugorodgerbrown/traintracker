from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import psycopg
import pytest

from traintracker import planner
from traintracker.config import UK_TZ, TimetableDB
from traintracker.errors import TrainTrackerError
from traintracker.timetable import (
    Timetable,
    TimetableMissing,
    build,
    fmt_minutes,
    parse_schedule,
    parse_time,
    runs_on,
)

from . import feedgen
from .conftest import timetable_db

DAY = date(2026, 10, 2)  # a Friday inside the fixture's date range


@pytest.fixture
def tt(tmp_path: Path) -> Timetable:
    extra = [
        # Overlay: on 3 Oct the 13:58 branch train runs 10 minutes later.
        feedgen.branch("B00013", 13 * 60 + 58 + 10, stp="O", start="2026-10-03", end="2026-10-03"),
        # Cancellation: on 4 Oct the 14:58 branch train doesn't run.
        feedgen.schedule("B00014", [], stp="C", start="2026-10-04", end="2026-10-04"),
        # Sunday-only train.
        feedgen.branch("S00001", 9 * 60, days="0000001"),
    ]
    db = timetable_db()
    meta = build(feedgen.feed(extra), db, today=date(2026, 9, 26))
    assert int(meta["public_schedules"]) > 0
    Timetable.clear_caches()
    return Timetable.open(db)


def test_parse_time() -> None:
    assert parse_time("0802") == 482
    assert parse_time("0802H") == 482
    assert parse_time(None) is None
    assert parse_time("") is None


def test_runs_on_days_and_range() -> None:
    assert runs_on("1111100", "2026-09-01", "2026-12-31", date(2026, 10, 2))  # Fri
    assert not runs_on("1111100", "2026-09-01", "2026-12-31", date(2026, 10, 3))  # Sat
    assert not runs_on("1111111", "2026-10-05", "2026-12-31", date(2026, 10, 2))


def test_midnight_is_monotonic() -> None:
    _row, stops = parse_schedule(feedgen.mainline("M23300", 23 * 60 + 30)["JsonScheduleV1"])  # type: ignore[misc]
    times = [t for s in stops for t in (s.arr, s.dep) if t is not None]
    assert times == sorted(times)
    assert stops[-1].arr == 23 * 60 + 30 + 57  # 00:27 next day, as 1467


def test_freight_and_expired_are_dropped(tt: Timetable) -> None:
    uids = {u for (u,) in tt.query("SELECT uid FROM schedules")}
    assert "F00001" not in uids
    assert "X00001" not in uids


def test_junction_without_crs_is_dropped(tt: Timetable) -> None:
    crs = {c for (c,) in tt.query("SELECT DISTINCT crs FROM stops")}
    assert None not in crs
    assert "SUY" in crs


def test_stp_overlay_and_cancellation(tt: Timetable) -> None:
    def branch_deps(day: date) -> list[str | None]:
        hits = tt.trips_at("MKT", day, 13 * 60, 15 * 60 + 30)
        return sorted(fmt_minutes(s.dep) for t, s in hits if t.uid.startswith("B"))

    assert branch_deps(date(2026, 10, 2)) == ["13:58", "14:58"]
    assert branch_deps(date(2026, 10, 3)) == ["14:08", "14:58"]  # overlay
    assert branch_deps(date(2026, 10, 4)) == ["13:58"]  # 14:58 cancelled


def test_days_runs(tt: Timetable) -> None:
    sunday = tt.trips_at("MKT", date(2026, 10, 4), 8 * 60, 9 * 60 + 30)
    friday = tt.trips_at("MKT", date(2026, 10, 2), 8 * 60, 9 * 60 + 30)
    assert any(t.uid == "S00001" for t, _ in sunday)
    assert not any(t.uid == "S00001" for t, _ in friday)


def test_previous_day_train_after_midnight(tt: Timetable) -> None:
    hits = tt.trips_at("COL", DAY, 0, 60, arriving=True)
    assert [(t.uid, t.run_date) for t, _ in hits] == [("M23300", date(2026, 10, 1))]


def _at(h: int, m: int, day: date = DAY) -> datetime:
    return datetime(day.year, day.month, day.day, h, m, tzinfo=UK_TZ)


def test_plan_lst_to_sudbury(tt: Timetable) -> None:
    journeys = planner.plan(tt, "LST", "SUY", _at(12, 5), count=2)
    assert len(journeys) == 2
    j = journeys[0]
    assert [(leg.board_at.crs, leg.alight_at.crs) for leg in j.legs] == [
        ("LST", "MKT"),
        ("MKT", "SUY"),
    ]
    assert j.depart.startswith("2026-10-02T13:00")
    assert j.arrive.startswith("2026-10-02T14:23")
    assert j.changes == 1
    assert j.interchange_minutes == [9]  # 13:49 arrive, 13:58 depart
    assert j.legs[0].operator == "Greater Anglia"
    assert j.legs[0].platform == "10"
    assert journeys[1].depart.startswith("2026-10-02T14:00")


def test_plan_respects_minimum_interchange(tt: Timetable) -> None:
    # With a 10-minute minimum every 9-minute Marks Tey change is too tight, so
    # the 13:00 connects into the following hour's branch train.
    j = planner.plan(tt, "LST", "SUY", _at(12, 5), count=1, mct=10)[0]
    assert j.arrive.startswith("2026-10-02T15:23")
    assert j.depart.startswith("2026-10-02T13:00")
    assert j.interchange_minutes == [69]


def test_plan_across_london(tt: Timetable) -> None:
    j = planner.plan(tt, "CBG", "SUY", _at(10, 55), count=1)[0]
    modes = [leg.mode for leg in j.legs]
    assert modes == ["train", "tube (approx.)", "train", "train"]
    assert j.legs[1].board_at.crs == "KGX" and j.legs[1].alight_at.crs == "LST"


def test_plan_direct(tt: Timetable) -> None:
    j = planner.plan(tt, "SRA", "COL", _at(9, 0), count=1)[0]
    assert j.changes == 0
    assert j.depart.startswith("2026-10-02T09:08")
    assert j.duration_minutes == 49


def test_plan_via(tt: Timetable) -> None:
    j = planner.plan(tt, "LST", "COL", _at(9, 0), via="CHM", count=1)[0]
    assert [leg.alight_at.crs for leg in j.legs] == ["CHM", "COL"]


def test_plan_no_route(tt: Timetable) -> None:
    assert planner.plan(tt, "SUY", "CBG", _at(23, 50), count=1) == []


def test_service_lookup(tt: Timetable) -> None:
    trip = tt.trip("B00013", date(2026, 10, 3))
    assert trip is not None
    assert fmt_minutes(trip.stops[0].dep) == "14:08"
    assert tt.trip("B00014", date(2026, 10, 4)) is None


def test_transfer_starts_when_previous_train_arrives(tt: Timetable) -> None:
    j = planner.plan(tt, "CBG", "SUY", _at(10, 55), count=1)[0]
    train, tube, *_ = j.legs
    assert tube.depart_scheduled == train.arrive_scheduled  # 11:50
    assert tube.arrive_scheduled < j.legs[2].depart_scheduled


@pytest.fixture
def tt_direct(tmp_path: Path) -> Timetable:
    # A slow through train LST 12:00 -> SUY 15:00 alongside the faster change.
    direct = feedgen.schedule(
        "D00001",
        [
            feedgen.location("LIVST", None, 12 * 60 + 2),
            feedgen.location("MRKSTEY", 13 * 60 + 50, 14 * 60 + 30),
            feedgen.location("SUDBURY", 15 * 60, None),
        ],
    )
    db = timetable_db("_direct")
    build(feedgen.feed([direct]), db, today=date(2026, 9, 26))
    Timetable.clear_caches()
    return Timetable.open(db)


def test_max_changes_finds_slower_direct_train(tt_direct: Timetable) -> None:
    [j] = planner.plan(tt_direct, "LST", "SUY", _at(11, 55), count=1, max_changes=0)
    assert j.changes == 0
    assert (j.depart[11:16], j.arrive[11:16]) == ("12:02", "15:00")


def test_fewest_changes_option_is_offered(tt_direct: Timetable) -> None:
    journeys = planner.plan(tt_direct, "LST", "SUY", _at(11, 55), count=3)
    assert journeys[0].changes == 1  # fastest first
    assert any(j.changes == 0 for j in journeys)


def test_walk_only_journey_is_returned_once(tt: Timetable) -> None:
    journeys = planner.plan(tt, "KGX", "STP", _at(12, 0), count=3)
    assert len(journeys) == 1
    assert [leg.mode for leg in journeys[0].legs] == ["walk"]


def test_leading_transfer_never_before_requested_time(tt: Timetable) -> None:
    j = planner.plan(tt, "KGX", "SUY", _at(12, 36), count=1)[0]
    assert j.depart >= "2026-10-02T12:36"
    assert j.legs[0].mode == "tube (approx.)"


def test_unadvertised_0000_stop_does_not_shift_the_day() -> None:
    rec = feedgen.mainline("M23200", 23 * 60 + 20)["JsonScheduleV1"]
    # An unadvertised stop at 23:40 working time, published as public "0000".
    rec["schedule_segment"]["schedule_location"].insert(
        1,
        {
            "tiploc_code": "BOWJ",
            "arrival": "2340",
            "departure": "2341",
            "public_arrival": "0000",
            "public_departure": "0000",
        },
    )
    parsed = parse_schedule(rec)
    assert parsed is not None
    _row, stops = parsed
    assert [s.tiploc for s in stops][:2] == ["LIVST", "STFD"]  # 0000 stop dropped
    assert stops[-1].arr == 23 * 60 + 20 + 57  # 00:17 next day, not two days on


def test_board_window_past_midnight_includes_tomorrows_trains(tmp_path: Path) -> None:
    early = feedgen.schedule(
        "E00010", [feedgen.location("LIVST", None, 10), feedgen.location("STFD", 18, None)]
    )
    db = timetable_db("_early")
    build(feedgen.feed([early]), db, today=date(2026, 9, 26))
    Timetable.clear_caches()
    hits = Timetable.open(db).trips_at("LST", DAY, 23 * 60, 26 * 60)
    uids = [(t.uid, t.run_date) for t, _ in hits]
    assert ("E00010", date(2026, 10, 3)) in uids
    assert ("M23300", DAY) in uids


def test_to_datetime_on_clock_change_days() -> None:
    from traintracker.timetable import to_datetime

    spring = date(2027, 3, 28)  # clocks go forward at 01:00 GMT
    assert to_datetime(spring, 90).isoformat() == "2027-03-28T02:30:00+01:00"
    assert to_datetime(spring, 180).isoformat() == "2027-03-28T03:00:00+01:00"
    assert to_datetime(date(2026, 10, 2), 600).isoformat() == "2026-10-02T10:00:00+01:00"


def test_failed_refresh_keeps_the_old_timetable(tt: Timetable) -> None:
    built_at = tt.meta["built_at"]
    no_passengers = [line for line in feedgen.feed() if "JsonScheduleV1" not in line]
    with pytest.raises(TrainTrackerError, match="no passenger schedules"):
        build(no_passengers, tt.db, today=date(2026, 9, 26))
    assert Timetable.open(tt.db).meta["built_at"] == built_at
    with psycopg.connect(tt.db.dsn) as con:
        staging = con.execute(
            "SELECT count(*) FROM pg_namespace WHERE nspname = %s", (f"{tt.db.schema}_build",)
        ).fetchone()
    assert staging == (0,)


def test_refresh_refuses_to_run_twice(tt: Timetable) -> None:
    with psycopg.connect(tt.db.dsn, autocommit=True) as other:
        other.execute("SELECT pg_advisory_lock(hashtext(%s))", (tt.db.schema,))
        with pytest.raises(TrainTrackerError, match="already running"):
            build(feedgen.feed(), tt.db, today=date(2026, 9, 26))


def test_tables_are_unlogged(tt: Timetable) -> None:
    rows = tt.query(
        "SELECT relname, relpersistence FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE nspname = %s AND relkind = 'r'",
        (tt.db.schema,),
    )
    assert dict(rows) == {"meta": "u", "schedules": "u", "stops": "u"}


def test_missing_database_url_is_explained() -> None:
    with pytest.raises(TimetableMissing, match="DATABASE_URL is not set"):
        Timetable.open(TimetableDB("", "timetable"))


def test_unreachable_database_is_explained() -> None:
    db = TimetableDB("postgresql://nobody@127.0.0.1:1/none", "timetable")
    with pytest.raises(TimetableMissing, match="can't be reached") as raised:
        Timetable.open(db)
    # Where the database is, and as whom, is not for whoever made the call.
    assert "127.0.0.1" not in str(raised.value) and "nobody" not in str(raised.value)
