"""Build small synthetic Network Rail SCHEDULE feeds in the real JSON format.

The network is a slice of East Anglia:

    CBG --(GN)--> KGX  ~~tube~~  LST --(LE)--> SRA --> CHM --> MKT --> COL
                                                             |
                                                             +--(LE)--> CWC --> BUE --> SUY
"""

from __future__ import annotations

import json
from typing import Any

START, END = "2026-09-01", "2026-12-31"

TIPLOCS = {
    "LIVST": "LST",
    "STFD": "SRA",
    "CHLMSFD": "CHM",
    "MRKSTEY": "MKT",
    "CLCHSTR": "COL",
    "CHAPPEL": "CWC",
    "BURES": "BUE",
    "SUDBURY": "SUY",
    "CAMBDGE": "CBG",
    "KNGX": "KGX",
    "BOWJ": None,  # junction: no CRS, never public
}


def _hhmm(minutes: int) -> str:
    minutes %= 24 * 60
    return f"{minutes // 60:02d}{minutes % 60:02d}"


def location(
    tiploc: str, arr: int | None, dep: int | None, platform: str | None = None
) -> dict[str, Any]:
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


def passing(tiploc: str, at: int) -> dict[str, Any]:
    return {
        "location_type": "LI",
        "record_identity": "LI",
        "tiploc_code": tiploc,
        "pass": _hhmm(at),
        "public_arrival": None,
        "public_departure": None,
    }


def schedule(
    uid: str,
    locations: list[dict[str, Any]],
    *,
    stp: str = "P",
    start: str = START,
    end: str = END,
    days: str = "1111111",
    atoc: str = "LE",
    category: str = "OO",
    headcode: str = "1A00",
) -> dict[str, Any]:
    return {
        "JsonScheduleV1": {
            "CIF_train_uid": uid,
            "CIF_stp_indicator": stp,
            "transaction_type": "Create",
            "schedule_start_date": start,
            "schedule_end_date": end,
            "schedule_days_runs": days,
            "atoc_code": atoc,
            "train_status": "P",
            "schedule_segment": {
                "signalling_id": headcode,
                "CIF_train_category": category,
                "schedule_location": locations,
            }
            if stp != "C"
            else {},
        }
    }


def mainline(uid: str, dep: int) -> dict[str, Any]:
    return schedule(
        uid,
        [
            location("LIVST", None, dep, "10"),
            location("STFD", dep + 7, dep + 8),
            passing("BOWJ", dep + 10),
            location("CHLMSFD", dep + 34, dep + 35),
            location("MRKSTEY", dep + 49, dep + 50, "1"),
            location("CLCHSTR", dep + 57, None),
        ],
        headcode="1P01",
    )


def branch(uid: str, dep: int, **kw: Any) -> dict[str, Any]:
    return schedule(
        uid,
        [
            location("MRKSTEY", None, dep, "3"),
            location("CHAPPEL", dep + 7, dep + 8),
            location("BURES", dep + 14, dep + 15),
            location("SUDBURY", dep + 25, None, "1"),
        ],
        headcode="2S01",
        **kw,
    )


def feed(extra: list[dict[str, Any]] | None = None) -> list[str]:
    records: list[dict[str, Any]] = [
        {"JsonTimetableV1": {"timestamp": 1790000000, "Metadata": {"type": "full"}}}
    ]
    for tiploc, crs in TIPLOCS.items():
        records.append(
            {"TiplocV1": {"transaction_type": "Create", "tiploc_code": tiploc, "crs_code": crs}}
        )
    for h in range(6, 23):
        records.append(mainline(f"M{h:05d}", h * 60))
        records.append(branch(f"B{h:05d}", h * 60 + 58))
    # Great Northern Cambridge -> Kings Cross, for cross-London planning.
    for h in range(6, 22):
        records.append(
            schedule(
                f"G{h:05d}",
                [location("CAMBDGE", None, h * 60), location("KNGX", h * 60 + 50, None)],
                atoc="GN",
                headcode="1K01",
            )
        )
    # Late train running past midnight.
    records.append(mainline("M23300", 23 * 60 + 30))
    # Freight: must be ignored.
    records.append(
        schedule(
            "F00001",
            [location("LIVST", None, 600), location("CLCHSTR", 700, None)],
            category="EE",
            atoc="ZZ",
        )
    )
    # Expired schedule: must be dropped at import.
    records.append(branch("X00001", 600, start="2025-01-01", end="2025-02-01"))
    records.extend(extra or [])
    return [json.dumps(r) for r in records]
