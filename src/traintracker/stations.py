"""Station lookup: resolve free text ("sudbury", "liverpool st", "LST") to CRS codes."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources

from rapidfuzz import fuzz, process

from traintracker.errors import StationNotFound


@dataclass(frozen=True, slots=True)
class Station:
    crs: str
    name: str
    lat: float
    lon: float


# No station name comes near this. Fuzzy matching takes time in proportion to the
# query's length, so anything longer is not matched at all.
MAX_QUERY = 80

# Common abbreviations people type, expanded before fuzzy matching.
_ABBREVIATIONS = {
    r"\bst\b": "street",
    r"\bstn\b": "",
    r"\bstation\b": "",
    r"\bjn\b": "junction",
    r"\bjct\b": "junction",
    r"\bint\b": "international",
    r"\bpkwy\b": "parkway",
    r"\bcent\b": "central",
    r"\brd\b": "road",
}


def _normalise(text: str) -> str:
    text = text.lower().replace("&", "and").replace("'", "").replace("\u2019", "")
    text = re.sub(r"[().,\-]", " ", text)
    for pattern, repl in _ABBREVIATIONS.items():
        text = re.sub(pattern, repl, text)
    return re.sub(r"\s+", " ", text).strip()


@lru_cache(maxsize=1)
def all_stations() -> tuple[Station, ...]:
    raw = resources.files("traintracker.data").joinpath("stations.json").read_text("utf-8")
    return tuple(Station(**s) for s in json.loads(raw))


@lru_cache(maxsize=1)
def _by_crs() -> dict[str, Station]:
    return {s.crs: s for s in all_stations()}


@lru_cache(maxsize=1)
def _choices() -> dict[str, str]:
    return {s.crs: _normalise(s.name) for s in all_stations()}


def by_crs(crs: str) -> Station | None:
    return _by_crs().get(crs.upper())


def search(query: str, limit: int = 5) -> list[tuple[Station, float]]:
    """Ranked matches with a 0-100 score. An exact CRS code always ranks first."""
    query = query.strip()
    if not query or len(query) > MAX_QUERY:
        return []
    results: list[tuple[Station, float]] = []
    exact = by_crs(query) if len(query) == 3 and query.isalpha() else None
    if exact:
        results.append((exact, 100.0))

    q = _normalise(query)
    matches = process.extract(q, _choices(), scorer=fuzz.WRatio, limit=limit + 5)
    for _name, score, crs in matches:
        station = _by_crs()[crs]
        name = _choices()[crs]
        # Reward names that equal, or start with, the query as whole words.
        # "kings cross" should find "London Kings Cross" outright.
        core = name.removeprefix("london ")
        if name == q:
            score = 100.0
        elif core == q:
            score = 98.0
        elif name.startswith(q + " ") or core.startswith(q + " "):
            score = max(score, 95.0)
        if exact and station.crs == exact.crs:
            continue
        results.append((station, float(score)))

    results.sort(key=lambda r: (-r[1], len(r[0].name)))
    return results[:limit]


def resolve(query: str) -> Station:
    """Resolve a query to exactly one station, or raise with the candidates.

    Accepts a CRS code (LST), a full name, or a close-enough name. Raises
    StationNotFound listing alternatives when the query is ambiguous, so the
    caller (the model) can ask the person which one they mean.
    """
    matches = search(query, limit=5)
    if not matches:
        raise StationNotFound(query, [])
    top, top_score = matches[0]
    if top_score == 100.0:
        return top
    runner_up = matches[1][1] if len(matches) > 1 else 0.0
    if top_score >= 98 and runner_up < 98:
        return top
    if top_score >= 90 and top_score - runner_up >= 5:
        return top
    raise StationNotFound(query, [f"{s.name} ({s.crs})" for s, _ in matches])
