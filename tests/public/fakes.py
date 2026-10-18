"""Fakes for the public site's tests: the site's address and a board fetch with no Darwin."""

from __future__ import annotations

import asyncio

from mcp.server.mcpserver.exceptions import ToolError

from apps.rail import stations
from apps.rail.schemas import Board, BoardService, Journey, JourneyLeg, StationRef

BASE = "https://tt.test"
# What the pages' fields are in these tests: views.fields is patched to return them.
FIELDS = {"mcp_url": f"{BASE}/mcp", "fair_use": "Be kind."}


def _board(crs: str = "LST", name: str = "London Liverpool Street") -> Board:
    """Three trains from a station: one on time, one late, one cancelled.

    Args:
        crs: The station's code.
        name: The station's name.

    Returns:
        The board, with one message on it.
    """

    def service(time: str, to: list[str], platform: str | None, **kw: object) -> BoardService:
        """One departure at ``time`` to ``to``, from ``platform``."""
        return BoardService(
            service_id=f"darwin:{time}",
            source="darwin",
            origin=[StationRef(name=name, crs=crs)],
            destination=[StationRef(name=d) for d in to],
            scheduled=time,
            platform=platform,
            **kw,
        )

    return Board(
        station=StationRef(name=name, crs=crs),
        board="departures",
        date="2026-10-02",
        source="darwin",
        services=[
            service("15:36", ["Colchester Town"], "14", expected="On time", status="on_time"),
            service("15:40", ["Norwich", "Clacton-on-Sea"], "9", expected="15:44*"),
            service("15:43", ["Cheshunt"], None, expected="15:43", status="cancelled"),
        ],
        messages=["Lifts are out of order at <this> station."],
    )


class FakeFetch:
    """A board fetch in place of Darwin's, which records what it was asked for.

    Attributes:
        calls: Each fetch's (station, destination, platform), in order.
        error: Set, every fetch fails with it, as Darwin's failures do.
    """

    def __init__(self, error: str | None = None) -> None:
        """Start with no calls, failing with ``error`` if it is given.

        Args:
            error: The message each fetch fails with, or None to succeed.
        """
        self.calls: list[tuple[str, str | None, str | None]] = []
        self.error = error

    async def __call__(
        self, crs: str, to: str | None, platform: str | None
    ) -> tuple[Board, list[str]]:
        """The three trains at Liverpool Street, cut down to ``to`` or ``platform``.

        Args:
            crs: The station's code.
            to: A destination's code, or None.
            platform: A platform, or None.

        Returns:
            The board and the platforms in use.

        Raises:
            ToolError: When ``error`` is set.
        """
        self.calls.append((crs, to, platform))
        await asyncio.sleep(0.01)  # long enough for other requests to arrive meanwhile
        if self.error:
            raise ToolError(self.error)
        board = _board()
        if platform:
            board.services = [s for s in board.services if s.platform == platform]
        if to:
            # Near enough for these trains: those that end there call there.
            name = stations.by_crs(to).name  # type: ignore[union-attr]
            board.services = [s for s in board.services if s.destination[0].name == name]
        return board, ["9", "14"]


def _leg(
    frm: str, to: str, dep: str, arr: str, towards: str, platform: str, **kw: object
) -> JourneyLeg:
    """One train of a journey on 2 October 2026, from ``frm`` to ``to``."""
    return JourneyLeg(
        service_id=f"tt:{frm}{dep}:2026-10-02",
        board_at=StationRef(name=stations.by_crs(frm).name, crs=frm),  # type: ignore[union-attr]
        alight_at=StationRef(name=stations.by_crs(to).name, crs=to),  # type: ignore[union-attr]
        destination=towards,
        depart_scheduled=f"2026-10-02T{dep}+01:00",
        arrive_scheduled=f"2026-10-02T{arr}+01:00",
        platform=platform,
        **kw,
    )


def _journey(first: JourneyLeg, then: JourneyLeg) -> Journey:
    """Two trains with a change between them."""
    return Journey(
        legs=[first, then],
        depart=first.depart_scheduled,
        arrive=then.arrive_scheduled,
        duration_minutes=88,
        changes=1,
    )


class FakeChanges:
    """Journeys with one change in place of the planner's, which records what it was asked for.

    Liverpool Street to Sudbury changes at Marks Tey; nowhere else has any.

    Attributes:
        calls: Each request's (station, destination), in order.
        error: Set, every request fails with it.
    """

    def __init__(self, error: str | None = None) -> None:
        """Start with no calls, failing with ``error`` if it is given.

        Args:
            error: The message each request fails with, or None to succeed.
        """
        self.calls: list[tuple[str, str]] = []
        self.error = error

    async def __call__(self, crs: str, to: str) -> list[Journey]:
        """Two journeys to Sudbury: one on time, one whose connection is late.

        Args:
            crs: The station's code.
            to: The destination's code.

        Returns:
            The journeys, earliest first.

        Raises:
            ToolError: When ``error`` is set.
        """
        self.calls.append((crs, to))
        if self.error:
            raise ToolError(self.error)
        if (crs, to) != ("LST", "SUY"):
            return []
        return [
            _journey(
                _leg("LST", "MKT", "15:08", "16:04", "Ipswich", "13", depart_expected="On time"),
                _leg(
                    "MKT",
                    "SUY",
                    "16:16",
                    "16:36",
                    "Sudbury (Suffolk)",
                    "3",
                    depart_expected="On time",
                ),
            ),
            _journey(
                _leg("LST", "MKT", "16:08", "17:04", "Ipswich", "14", depart_expected="On time"),
                _leg(
                    "MKT",
                    "SUY",
                    "17:16",
                    "17:36",
                    "Sudbury (Suffolk)",
                    "3",
                    depart_expected="17:21*",
                ),
            ),
        ]
