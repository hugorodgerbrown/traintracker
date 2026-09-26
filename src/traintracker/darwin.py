"""Darwin (National Rail) live boards via the Rail Data Marketplace LDBWS REST API."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal
from urllib.parse import quote

import httpx

from traintracker.config import UK_TZ, Settings
from traintracker.errors import NotConfigured, ServiceNotFound
from traintracker.http import TTLCache, json_body, raise_for_status
from traintracker.models import (
    Board,
    BoardService,
    CallingPoint,
    ServiceDetail,
    StationRef,
    Status,
)

SOURCE = "Darwin"
LIVE_TTL = 20.0
_TIME = re.compile(r"^\d{2}:\d{2}$")
_TAGS = re.compile(r"<[^>]+>")


def _refs(items: list[dict[str, Any]] | None) -> list[StationRef]:
    return [StationRef(name=i.get("locationName", "?"), crs=i.get("crs")) for i in items or []]


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def status_from(
    scheduled: str | None, expected: str | None, cancelled: bool
) -> tuple[Status, int | None]:
    """Map Darwin's etd/eta strings ("On time", "12:14", "Delayed", ...) to a status."""
    if cancelled or (expected or "").lower() == "cancelled":
        return "cancelled", None
    if not expected:
        return "unknown", None
    exp = expected.rstrip("*").strip()
    lowered = exp.lower()
    if lowered == "on time":
        return "on_time", 0
    if lowered == "delayed":
        return "delayed", None
    if lowered == "no report":
        return "no_report", None
    if scheduled and _TIME.match(exp) and _TIME.match(scheduled):
        diff = _minutes(exp) - _minutes(scheduled)
        if diff < -12 * 60:  # crossed midnight
            diff += 24 * 60
        elif diff > 12 * 60:
            diff -= 24 * 60
        if diff > 0:
            return "late", diff
        if diff < 0:
            return "early", diff
        return "on_time", 0
    return "unknown", None


def _calling_points(
    groups: list[dict[str, Any]] | None, *, previous: bool = False
) -> list[CallingPoint]:
    """Darwin nests calling points as [{callingPoint: [...]}, ...] (one list per portion)."""
    points: list[CallingPoint] = []
    for group in groups or []:
        for cp in group.get("callingPoint") or []:
            points.append(
                CallingPoint(
                    station=StationRef(name=cp.get("locationName", "?"), crs=cp.get("crs")),
                    # Darwin gives one time per stop: the departure for stops
                    # already passed, the arrival for stops still to come.
                    scheduled_arrival=None if previous else cp.get("st"),
                    scheduled_departure=cp.get("st") if previous else None,
                    expected=cp.get("et"),
                    actual=cp.get("at"),
                    cancelled=bool(cp.get("isCancelled")),
                )
            )
    return points


def _messages(data: dict[str, Any]) -> list[str]:
    out = []
    for m in data.get("nrccMessages") or []:
        text = m.get("Value") or m.get("value") or ""
        text = _TAGS.sub("", text).replace("&amp;", "&").strip()
        if text:
            out.append(text)
    return out


def _service(raw: dict[str, Any], board: Literal["departures", "arrivals"]) -> BoardService:
    scheduled = raw.get("std") if board == "departures" else raw.get("sta")
    expected = raw.get("etd") if board == "departures" else raw.get("eta")
    cancelled = bool(raw.get("isCancelled"))
    status, delay = status_from(scheduled, expected, cancelled)
    cp_key = "subsequentCallingPoints" if board == "departures" else "previousCallingPoints"
    cps = raw.get(cp_key)
    return BoardService(
        service_id=f"darwin:{raw.get('serviceID')}",
        source="darwin",
        operator=raw.get("operator"),
        mode=raw.get("serviceType") or "train",
        origin=_refs(raw.get("origin")),
        destination=_refs(raw.get("destination")),
        scheduled=scheduled,
        expected=expected,
        platform=raw.get("platform"),
        status=status,
        delay_minutes=delay,
        reason=raw.get("cancelReason") or raw.get("delayReason"),
        calling_points=_calling_points(cps, previous=board == "arrivals") if cps else None,
    )


def _board_date(data: dict[str, Any]) -> str:
    generated = data.get("generatedAt")
    if generated:
        try:
            return datetime.fromisoformat(generated).astimezone(UK_TZ).date().isoformat()
        except ValueError:
            pass
    return datetime.now(UK_TZ).date().isoformat()


class DarwinClient:
    def __init__(self, settings: Settings, http: httpx.AsyncClient) -> None:
        self.s = settings
        self.http = http
        self.cache = TTLCache()

    async def _get(self, url: str, key: str, params: dict[str, Any]) -> dict[str, Any]:
        cache_key = (url, tuple(sorted(params.items())))
        if (hit := self.cache.get(cache_key)) is not None:
            return hit  # type: ignore[no-any-return]
        resp = await self.http.get(
            url, params=params, headers={"x-apikey": key}, timeout=self.s.http_timeout
        )
        raise_for_status(SOURCE, resp)
        data = json_body(SOURCE, resp)
        self.cache.set(cache_key, data, LIVE_TTL)
        return data

    async def board(
        self,
        crs: str,
        board: Literal["departures", "arrivals"],
        *,
        filter_crs: str | None = None,
        rows: int = 10,
        offset_minutes: int = 0,
        window_minutes: int = 120,
    ) -> Board:
        if board == "departures":
            if not self.s.darwin_key:
                raise NotConfigured("DARWIN_API_KEY is not set.")
            url, key, op = self.s.darwin_departures_url, self.s.darwin_key, "GetDepBoardWithDetails"
        else:
            if not (self.s.darwin_arrivals_key and self.s.darwin_arrivals_url):
                raise NotConfigured("DARWIN_ARRIVALS_API_KEY / DARWIN_ARRIVALS_URL are not set.")
            url, key = self.s.darwin_arrivals_url, self.s.darwin_arrivals_key
            op = "GetArrBoardWithDetails"

        params: dict[str, Any] = {
            "numRows": max(1, min(rows, 149)),
            "timeOffset": max(-119, min(offset_minutes, 119)),
            "timeWindow": max(1, min(window_minutes, 119)),
        }
        if filter_crs:
            params["filterCrs"] = filter_crs
            params["filterType"] = "to" if board == "departures" else "from"

        data = await self._get(f"{url}/{op}/{crs}", key, params)
        services = [_service(s, board) for s in data.get("trainServices") or []]
        services += [_service(s, board) for s in data.get("busServices") or []]
        services += [_service(s, board) for s in data.get("ferryServices") or []]
        # Reference 12h before the board time: very late trains still sort first.
        ref = _ref_minutes(data) + offset_minutes - 12 * 60
        services.sort(key=lambda s: _sort_key(s.scheduled, ref))
        return Board(
            station=StationRef(name=data.get("locationName") or crs, crs=data.get("crs") or crs),
            board=board,
            date=_board_date(data),
            source="darwin",
            filter=StationRef(name=data.get("filterLocationName") or filter_crs, crs=filter_crs)
            if filter_crs
            else None,
            services=services[:rows],
            messages=_messages(data),
        )

    async def service(self, service_id: str) -> ServiceDetail:
        if not self.s.darwin_service_key:
            raise NotConfigured("DARWIN_SERVICE_API_KEY (or DARWIN_API_KEY) is not set.")
        url = f"{self.s.darwin_service_url}/GetServiceDetails/{quote(service_id, safe='')}"
        data = await self._get(url, self.s.darwin_service_key, {})
        if not data:
            raise ServiceNotFound(
                "Darwin no longer has that service. Darwin IDs expire soon after the train runs; "
                "use an rtt: service ID for past services."
            )
        here = CallingPoint(
            station=StationRef(name=data.get("locationName", "?"), crs=data.get("crs")),
            scheduled_arrival=data.get("sta"),
            scheduled_departure=data.get("std"),
            expected=data.get("etd") or data.get("eta"),
            actual=data.get("atd") or data.get("ata"),
            platform=data.get("platform"),
            cancelled=bool(data.get("isCancelled")),
        )
        points = [
            *_calling_points(data.get("previousCallingPoints"), previous=True),
            here,
            *_calling_points(data.get("subsequentCallingPoints")),
        ]
        return ServiceDetail(
            service_id=f"darwin:{service_id}",
            source="darwin",
            operator=data.get("operator"),
            date=_board_date(data),
            mode=data.get("serviceType") or "train",
            origin=[points[0].station] if points else [],
            destination=[points[-1].station] if points else [],
            cancelled=bool(data.get("isCancelled")),
            reason=data.get("cancelReason") or data.get("delayReason"),
            calling_points=points,
        )


def _ref_minutes(data: dict[str, Any]) -> int:
    generated = data.get("generatedAt")
    try:
        dt = datetime.fromisoformat(generated).astimezone(UK_TZ) if generated else None
    except ValueError:
        dt = None
    dt = dt or datetime.now(UK_TZ)
    return dt.hour * 60 + dt.minute


def _sort_key(hhmm: str | None, ref_minutes: int) -> int:
    """Minutes after the board's reference time, so 23:50 sorts before 00:10."""
    if not hhmm or not _TIME.match(hhmm):
        return 10_000
    return (_minutes(hhmm) - ref_minutes) % (24 * 60)
