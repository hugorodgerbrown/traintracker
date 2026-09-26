"""Realtime Trains next-generation API (data.rtt.io).

Covers any date (subject to your token's history limits), live running,
full calling patterns, and the data the journey planner needs.
Spec: https://github.com/realtimetrains/api-specification
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Literal

import httpx

from traintracker.config import UK_TZ, Settings
from traintracker.errors import NotConfigured, ServiceNotFound, UpstreamError
from traintracker.http import TTLCache, json_body, raise_for_status
from traintracker.models import (
    Board,
    BoardService,
    CallingPoint,
    ServiceDetail,
    StationRef,
    Status,
)
from traintracker.stations import by_crs

SOURCE = "Realtime Trains"
LIVE_TTL = 30.0
PUBLIC_DISPLAY = {"CALL", "CANCELLED", "STARTS", "TERMINATES"}
MAX_QUERY = timedelta(hours=23, minutes=59)


# ---------------------------------------------------------------- time helpers


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UK_TZ)
    return dt.astimezone(UK_TZ)


def hhmm(dt: datetime | None) -> str | None:
    return dt.strftime("%H:%M") if dt else None


def local_iso(dt: datetime) -> str:
    """RTT assumes the location's local time when no offset is given."""
    return dt.astimezone(UK_TZ).replace(tzinfo=None, microsecond=0).isoformat()


def minutes_between(a: datetime, b: datetime) -> int:
    return round((b - a).total_seconds() / 60)


# ------------------------------------------------------------- parse helpers


@dataclass(frozen=True, slots=True)
class Timing:
    scheduled: datetime | None
    expected: datetime | None
    actual: datetime | None
    no_report: bool
    cancelled: bool

    @property
    def best(self) -> datetime | None:
        """Most reliable known time: actual, then forecast, then booked."""
        return self.actual or self.expected or self.scheduled


def timing(block: dict[str, Any] | None) -> Timing:
    block = block or {}
    return Timing(
        scheduled=parse_dt(block.get("scheduleAdvertised")),
        expected=parse_dt(block.get("realtimeForecast") or block.get("realtimeEstimate")),
        actual=parse_dt(block.get("realtimeActual")),
        no_report=bool(block.get("realtimeNoReport")),
        cancelled=bool(block.get("isCancelled")),
    )


def _pair_refs(items: list[dict[str, Any]] | None) -> list[StationRef]:
    return [_ref(i.get("location")) for i in items or []]


def _ref(loc: dict[str, Any] | None) -> StationRef:
    loc = loc or {}
    codes = loc.get("shortCodes") or []
    return StationRef(name=loc.get("description") or "?", crs=codes[0] if codes else None)


def _platform(meta: dict[str, Any] | None) -> str | None:
    plat = (meta or {}).get("platform") or {}
    value = plat.get("actual") or plat.get("forecast") or plat.get("planned")
    return str(value) if value else None


def _mode(schedule: dict[str, Any]) -> str:
    mode = (schedule.get("modeType") or "TRAIN").upper()
    return {
        "TRAIN": "train",
        "SHIP": "ferry",
        "BUS": "bus",
        "SCHEDULED_BUS": "bus",
        "REPLACEMENT_BUS": "replacement bus",
    }.get(mode, mode.lower())


def _reason(reasons: list[dict[str, Any]] | None) -> str | None:
    for r in reasons or []:
        text = r.get("longText") or r.get("shortText")
        if text:
            return str(text)
    return None


def service_id(schedule: dict[str, Any]) -> str:
    return f"rtt:{schedule.get('identity')}:{schedule.get('departureDate')}"


def parse_service_id(value: str) -> tuple[str, str]:
    """'rtt:L01525:2025-10-26' -> ('L01525', '2025-10-26')."""
    parts = value.removeprefix("rtt:").removeprefix("gb-nr:").split(":")
    if len(parts) != 2:
        raise ServiceNotFound(f"'{value}' is not a Realtime Trains service ID (rtt:IDENTITY:DATE).")
    return parts[0], parts[1]


def expected_text(t: Timing) -> str | None:
    if t.cancelled:
        return "Cancelled"
    if t.actual:
        return hhmm(t.actual)
    if t.expected:
        return hhmm(t.expected)
    if t.no_report:
        return "No report"
    return "On time" if t.scheduled and t.scheduled > datetime.now(UK_TZ) else None


def status_of(t: Timing, cancelled: bool) -> tuple[Status, int | None]:
    if cancelled or t.cancelled:
        return "cancelled", None
    live = t.actual or t.expected
    if t.scheduled and live:
        diff = minutes_between(t.scheduled, live)
        if diff > 0:
            return "late", diff
        if diff < 0:
            return "early", diff
        return "on_time", 0
    if t.no_report:
        return "no_report", None
    return ("on_time", 0) if t.scheduled else ("unknown", None)


def lineup_is_public(item: dict[str, Any], board: Literal["departures", "arrivals"]) -> bool:
    schedule = item.get("scheduleMetadata") or {}
    if schedule.get("inPassengerService") is False:
        return False
    td = item.get("temporalData") or {}
    if td.get("displayAs") not in PUBLIC_DISPLAY:
        return False
    key = "departure" if board == "departures" else "arrival"
    return bool((td.get(key) or {}).get("scheduleAdvertised"))


def board_service(item: dict[str, Any], board: Literal["departures", "arrivals"]) -> BoardService:
    td = item.get("temporalData") or {}
    schedule = item.get("scheduleMetadata") or {}
    t = timing(td.get("departure" if board == "departures" else "arrival"))
    display = td.get("displayAs")
    # A train altered to terminate here will not depart; one altered to start
    # here will not arrive.
    altered_away = (board == "departures" and display == "TERMINATES") or (
        board == "arrivals" and display == "STARTS"
    )
    cancelled = display == "CANCELLED" or altered_away
    status, delay = status_of(t, cancelled)
    reason = _reason(item.get("reasons"))
    if altered_away and not reason:
        reason = "Terminates here" if board == "departures" else "Starts here"
    return BoardService(
        service_id=service_id(schedule),
        source="rtt",
        operator=(schedule.get("operator") or {}).get("name"),
        mode=_mode(schedule),
        origin=_pair_refs(item.get("origin")),
        destination=_pair_refs(item.get("destination")),
        scheduled=hhmm(t.scheduled),
        expected="Cancelled" if cancelled else expected_text(t),
        platform=_platform(item.get("locationMetadata")),
        status=status,
        delay_minutes=delay,
        reason=reason,
    )


def calling_points(locations: list[dict[str, Any]]) -> list[CallingPoint]:
    points = []
    for loc in locations:
        td = loc.get("temporalData") or {}
        if td.get("displayAs") not in PUBLIC_DISPLAY:
            continue
        if (td.get("scheduledCallType") or "").startswith("OPERATIONAL"):
            continue
        arr, dep = timing(td.get("arrival")), timing(td.get("departure"))
        live = dep if dep.scheduled else arr
        points.append(
            CallingPoint(
                station=_ref(loc.get("location")),
                scheduled_arrival=hhmm(arr.scheduled),
                scheduled_departure=hhmm(dep.scheduled),
                expected=expected_text(live),
                actual=hhmm(live.actual),
                platform=_platform(loc.get("locationMetadata")),
                cancelled=td.get("displayAs") == "CANCELLED" or live.cancelled,
            )
        )
    return points


# --------------------------------------------------------------------- client


class RttClient:
    def __init__(self, settings: Settings, http: httpx.AsyncClient) -> None:
        self.s = settings
        self.http = http
        self.cache = TTLCache()
        self._access: tuple[str, datetime] | None = None
        self._token_lock = asyncio.Lock()

    async def _token(self) -> str:
        if self.s.rtt_access_token:
            return self.s.rtt_access_token
        if not self.s.rtt_refresh_token:
            raise NotConfigured(
                "RTT_ACCESS_TOKEN or RTT_REFRESH_TOKEN is not set. "
                "Get one at https://api-portal.rtt.io."
            )
        async with self._token_lock:
            now = datetime.now(UK_TZ)
            if self._access and self._access[1] - timedelta(minutes=1) > now:
                return self._access[0]
            resp = await self.http.get(
                f"{self.s.rtt_base_url}/api/get_access_token",
                headers={"Authorization": f"Bearer {self.s.rtt_refresh_token}"},
                timeout=self.s.http_timeout,
            )
            raise_for_status(SOURCE, resp)
            data = json_body(SOURCE, resp)
            expiry = parse_dt(data.get("validUntil")) or now + timedelta(minutes=10)
            token = data.get("token")
            if not token:
                raise UpstreamError(f"{SOURCE} did not return an access token.")
            self._access = (str(token), expiry)
            return self._access[0]

    async def _get(self, path: str, params: dict[str, Any], ttl: float) -> dict[str, Any] | None:
        """GET a JSON document. Returns None for 204 (no services) and 404."""
        clean = {k: v for k, v in params.items() if v is not None}
        cache_key = (path, tuple(sorted(clean.items())))
        if (hit := self.cache.get(cache_key)) is not None:
            return hit or None  # cached empties are stored as {}
        resp = await self.http.get(
            f"{self.s.rtt_base_url}{path}",
            params=clean,
            headers={"Authorization": f"Bearer {await self._token()}"},
            timeout=self.s.http_timeout,
        )
        if resp.status_code in (204, 404):
            self.cache.set(cache_key, {}, ttl)
            return None
        if resp.status_code == 400:
            raise UpstreamError(f"{SOURCE} rejected the query: {resp.text[:200]}")
        raise_for_status(SOURCE, resp)
        data = json_body(SOURCE, resp)
        self.cache.set(cache_key, data, ttl)
        return data

    @staticmethod
    def _ttl(start: datetime) -> float:
        # Anything more than a few hours away changes rarely; cache longer.
        return LIVE_TTL if abs(start - datetime.now(UK_TZ)) < timedelta(hours=3) else 600.0

    async def lineup(
        self,
        crs: str,
        start: datetime,
        end: datetime,
        *,
        filter_to: str | None = None,
        filter_from: str | None = None,
    ) -> list[dict[str, Any]]:
        end = min(end, start + MAX_QUERY)
        data = await self._get(
            "/gb-nr/location",
            {
                "code": crs,
                "timeFrom": local_iso(start),
                "timeTo": local_iso(end),
                "filterTo": filter_to,
                "filterFrom": filter_from,
            },
            self._ttl(start),
        )
        return list((data or {}).get("services") or [])

    async def board(
        self,
        crs: str,
        board: Literal["departures", "arrivals"],
        start: datetime,
        *,
        window_minutes: int = 120,
        filter_crs: str | None = None,
        rows: int = 15,
    ) -> Board:
        items = await self.lineup(
            crs,
            start,
            start + timedelta(minutes=window_minutes),
            filter_to=filter_crs if board == "departures" else None,
            filter_from=filter_crs if board == "arrivals" else None,
        )
        services = [board_service(i, board) for i in items if lineup_is_public(i, board)]
        return Board(
            station=StationRef(name=_station_name(crs), crs=crs),
            board=board,
            date=start.date().isoformat(),
            source="rtt",
            filter=(
                StationRef(name=_station_name(filter_crs), crs=filter_crs) if filter_crs else None
            ),
            services=services[:rows],
        )

    async def service_raw(self, identity: str, departure_date: str) -> dict[str, Any]:
        try:
            run_date = date.fromisoformat(departure_date)
        except ValueError as exc:
            raise ServiceNotFound(f"Bad service date '{departure_date}'.") from exc
        ttl = LIVE_TTL if abs((run_date - datetime.now(UK_TZ).date()).days) <= 1 else 600.0
        data = await self._get(
            "/gb-nr/service", {"identity": identity, "departureDate": departure_date}, ttl
        )
        if not data or not data.get("service"):
            raise ServiceNotFound(f"Realtime Trains has no service {identity} on {departure_date}.")
        return data["service"]  # type: ignore[no-any-return]

    async def service(self, sid: str) -> ServiceDetail:
        identity, departure_date = parse_service_id(sid)
        svc = await self.service_raw(identity, departure_date)
        schedule = svc.get("scheduleMetadata") or {}
        points = calling_points(svc.get("locations") or [])
        return ServiceDetail(
            service_id=service_id(schedule) if schedule.get("identity") else sid,
            source="rtt",
            operator=(schedule.get("operator") or {}).get("name"),
            headcode=schedule.get("trainReportingIdentity"),
            date=schedule.get("departureDate") or departure_date,
            mode=_mode(schedule),
            origin=_pair_refs(svc.get("origin")),
            destination=_pair_refs(svc.get("destination")),
            cancelled=bool(points) and all(p.cancelled for p in points),
            reason=_reason(svc.get("reasons")),
            calling_points=points,
        )


def _station_name(crs: str | None) -> str:
    station = by_crs(crs) if crs else None
    return station.name if station else (crs or "?")
