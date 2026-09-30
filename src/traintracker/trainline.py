"""Links to Trainline's live page for one train: its stops and how it is running.

The address is <live>/departures/<station>/<token>. The token is the base64 of
"/callingPattern/<CIF train UID>/<run date>", so a timetable service has what it
takes; a Darwin service ID carries no UID. The station only titles the page
("Departures from ..."), and is named by a slug of Trainline's name for it.

Trainline publishes no specification for these addresses. They were read off its
live boards in September 2026, and the slugs checked against the sitemap of its
live pages. A train it doesn't know gets that station's board instead.
"""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Iterable
from datetime import date
from functools import lru_cache
from importlib import resources

from traintracker import stations


@lru_cache(maxsize=1)
def _exceptions() -> dict[str, str | None]:
    """Stations whose slug isn't the one their name gives (Trainline calls Swindon
    "Swindon (Wilts)"), and, as null, stations Trainline has no live board for."""
    raw = resources.files("traintracker.data").joinpath("trainline_slugs.json").read_text("utf-8")
    slugs: dict[str, str | None] = json.loads(raw)
    return slugs


def _slug(name: str) -> str:
    name = name.lower().replace("&", "and").replace("'", "").replace(".", "")
    return re.sub(r"[^a-z0-9]+", "-", name).strip("-")


def station_slug(crs: str) -> str | None:
    """The station's name in Trainline's live addresses; None if it has no board there."""
    crs = crs.upper()
    if crs in _exceptions():
        return _exceptions()[crs]
    station = stations.by_crs(crs)
    return _slug(station.name) if station else None


def train_url(live_url: str, uid: str, run_date: date, crs_codes: Iterable[str]) -> str | None:
    """Trainline's page for one train, titled for the first of `crs_codes` it has a
    board for. None if it has a board for none of them."""
    slug = next((s for s in map(station_slug, crs_codes) if s), None)
    if slug is None:
        return None
    pattern = f"/callingPattern/{uid}/{run_date.isoformat()}"
    token = base64.b64encode(pattern.encode()).decode()
    return f"{live_url.rstrip('/')}/departures/{slug}/{token}"
