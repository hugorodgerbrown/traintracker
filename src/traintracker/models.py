"""Normalised output models shared by both data sources.

Times are UK local. Board times are "HH:MM"; journey times are ISO 8601 with
offset, because a journey can cross midnight.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, PrivateAttr

Source = Literal["darwin", "timetable"]
PlatformSource = Literal["live", "booked"]
Status = Literal[
    "on_time", "late", "early", "cancelled", "delayed", "no_report", "scheduled", "unknown"
]
TRAINLINE_URL = (
    "This train's page on Trainline: its stops and live running. Timetable ('tt:') services only."
)


class StationRef(BaseModel):
    name: str
    crs: str | None = None


class CallingPoint(BaseModel):
    station: StationRef
    scheduled_arrival: str | None = None
    scheduled_departure: str | None = None
    expected: str | None = Field(None, description="Forecast time, or 'On time', 'Delayed', etc.")
    actual: str | None = Field(None, description="Actual time if the train has reported here.")
    platform: str | None = None
    cancelled: bool = False


class BoardService(BaseModel):
    service_id: str = Field(
        description="Pass to service_details. Prefixed 'darwin:' or 'tt:' (timetable)."
    )
    source: Source
    operator: str | None = None
    mode: str = Field("train", description="train, bus (incl. replacement bus) or ferry.")
    origin: list[StationRef]
    destination: list[StationRef]
    scheduled: str | None = Field(None, description="Booked time at this station (HH:MM).")
    expected: str | None = Field(None, description="Forecast/actual time or status text.")
    platform: str | None = None
    platform_source: PlatformSource | None = Field(
        None,
        description="'live' if announced by the live feed, 'booked' if from the timetable "
        "(can still change).",
    )
    status: Status = "unknown"
    delay_minutes: int | None = None
    reason: str | None = Field(None, description="Delay or cancellation reason, if given.")
    calling_points: list[CallingPoint] | None = Field(
        None, description="Subsequent stops (departures) or previous stops (arrivals)."
    )
    trainline_url: str | None = Field(None, description=TRAINLINE_URL)


class Board(BaseModel):
    station: StationRef
    board: Literal["departures", "arrivals"]
    date: str
    source: Source
    filter: StationRef | None = None
    services: list[BoardService]
    messages: list[str] = Field(default_factory=list, description="Station/network alerts.")
    platform_available: bool | None = Field(
        None, description="False if the live feed publishes no platforms for this station."
    )


class ServiceDetail(BaseModel):
    service_id: str
    source: Source
    operator: str | None = None
    headcode: str | None = None
    date: str | None = None
    mode: str = "train"
    origin: list[StationRef]
    destination: list[StationRef]
    cancelled: bool = False
    reason: str | None = None
    calling_points: list[CallingPoint]
    trainline_url: str | None = Field(None, description=TRAINLINE_URL)


class JourneyLeg(BaseModel):
    service_id: str = Field(description="Empty for walk/Tube transfers.")
    operator: str | None = None
    mode: str = "train"
    board_at: StationRef
    alight_at: StationRef
    destination: str = Field(description="Where the train is ultimately heading (for the board).")
    depart_scheduled: str
    depart_expected: str | None = None
    arrive_scheduled: str
    arrive_expected: str | None = None
    platform: str | None = None
    cancelled: bool = False
    trainline_url: str | None = Field(None, description=TRAINLINE_URL)
    # Every stop of the train, for its Trainline link when neither end of the
    # leg has a board there. Not part of the output.
    _calls: tuple[str, ...] = PrivateAttr(default=())


class Journey(BaseModel):
    legs: list[JourneyLeg]
    depart: str
    arrive: str
    duration_minutes: int
    changes: int
    interchange_minutes: list[int] = Field(default_factory=list)
    connection_at_risk: bool = Field(
        False, description="True if live forecasts leave less than the minimum interchange time."
    )


class JourneyPlan(BaseModel):
    origin: StationRef
    destination: StationRef
    searched_from: str
    journeys: list[Journey]
    interchanges_considered: list[StationRef] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class PlatformCheck(BaseModel):
    station: StationRef
    service: BoardService
    platform: str | None = None
    platform_source: PlatformSource | None = None
    minutes_to_departure: int | None = Field(
        None, description="From now to the expected (else booked) departure."
    )
    note: str


class StationMatch(BaseModel):
    name: str
    crs: str
    score: float
