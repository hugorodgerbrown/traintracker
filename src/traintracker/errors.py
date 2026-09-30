"""Errors raised by traintracker. Messages are written for the model to relay."""

from __future__ import annotations


class TrainTrackerError(Exception):
    """Base error. The message is safe to show to the person."""


class StationNotFound(TrainTrackerError):
    def __init__(self, query: str, candidates: list[str]) -> None:
        self.query = query
        self.candidates = candidates
        if candidates:
            msg = (
                f"'{query}' is ambiguous. Did you mean one of: "
                + "; ".join(candidates)
                + "? Ask which one, or pass the 3-letter CRS code."
            )
        else:
            msg = f"No station matches '{query}'. Try find_station with a different spelling."
        super().__init__(msg)


class NotConfigured(TrainTrackerError):
    """A data source needed for this request has no credentials."""


class UpstreamError(TrainTrackerError):
    """The upstream API failed or rejected the request."""


class RateLimited(UpstreamError):
    def __init__(self, source: str, retry_after: str | None) -> None:
        wait = f" Retry after {retry_after}s." if retry_after else ""
        super().__init__(f"{source} rate limit reached.{wait}")


class AllowanceSpent(UpstreamError):
    """The server has sent Darwin as many requests as it allows itself in a day."""

    def __init__(self) -> None:
        super().__init__(
            "This server has used today's allowance of National Rail live data (Darwin); "
            "it resets at midnight, UK time."
        )


class ServiceNotFound(TrainTrackerError):
    pass
