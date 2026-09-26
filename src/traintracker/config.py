"""Configuration from environment variables. Nothing is read from disk."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

UK_TZ = ZoneInfo("Europe/London")

# Rail Data Marketplace product base URLs. Each RDM product has its own URL and
# its own consumer key; copy both from the product's "Specification" tab.
DARWIN_DEPARTURES_URL = (
    "https://api1.raildata.org.uk/1010-live-departure-board-dep1_2/LDBWS/api/20220120"
)
DARWIN_SERVICE_URL = "https://api1.raildata.org.uk/1010-service-details1_2/LDBWS/api/20220120"
RTT_BASE_URL = "https://data.rtt.io"
NR_SCHEDULE_URL = (
    "https://publicdatafeeds.networkrail.co.uk/ntrod/CifFileAuthenticate"
    "?type=CIF_ALL_FULL_DAILY&day=toc-full"
)


def default_data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME")
    return Path(base) / "traintracker" if base else Path.home() / ".traintracker"


def load_dotenv(path: Path | None = None) -> None:
    """Load KEY=VALUE lines from .env into os.environ without overriding real env vars.

    Lets secrets live in a git-ignored .env in the project folder rather than
    in the Claude desktop config.
    """
    if os.environ.get("TRAINTRACKER_NO_DOTENV"):
        return
    path = path or Path.cwd() / ".env"
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.removeprefix("export ").partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].rstrip()  # inline comment
        os.environ.setdefault(key, value)


def _env(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


@dataclass(frozen=True)
class Settings:
    darwin_key: str | None = field(repr=False)
    darwin_departures_url: str
    darwin_service_key: str | None = field(repr=False)
    darwin_service_url: str
    darwin_arrivals_key: str | None = field(repr=False)
    darwin_arrivals_url: str | None
    rtt_access_token: str | None = field(repr=False)
    rtt_refresh_token: str | None = field(repr=False)
    rtt_base_url: str
    nr_username: str | None
    nr_password: str | None = field(repr=False)
    nr_schedule_url: str
    data_dir: Path
    timetable_max_age_hours: float
    min_interchange_minutes: int
    http_timeout: float

    @property
    def timetable_path(self) -> Path:
        return self.data_dir / "timetable.sqlite"

    @property
    def has_nr(self) -> bool:
        return self.nr_username is not None and self.nr_password is not None

    @property
    def has_darwin(self) -> bool:
        return self.darwin_key is not None

    @property
    def has_darwin_arrivals(self) -> bool:
        return self.darwin_arrivals_key is not None and self.darwin_arrivals_url is not None

    @property
    def has_rtt(self) -> bool:
        return self.rtt_access_token is not None or self.rtt_refresh_token is not None

    @classmethod
    def from_env(cls) -> Settings:
        darwin_key = _env("DARWIN_API_KEY")
        return cls(
            darwin_key=darwin_key,
            darwin_departures_url=_env("DARWIN_DEPARTURES_URL") or DARWIN_DEPARTURES_URL,
            # Service details is a separate RDM product; fall back to the board key.
            darwin_service_key=_env("DARWIN_SERVICE_API_KEY") or darwin_key,
            darwin_service_url=_env("DARWIN_SERVICE_URL") or DARWIN_SERVICE_URL,
            darwin_arrivals_key=_env("DARWIN_ARRIVALS_API_KEY"),
            darwin_arrivals_url=_env("DARWIN_ARRIVALS_URL"),
            rtt_access_token=_env("RTT_ACCESS_TOKEN"),
            rtt_refresh_token=_env("RTT_REFRESH_TOKEN"),
            rtt_base_url=_env("RTT_BASE_URL") or RTT_BASE_URL,
            nr_username=_env("NR_USERNAME"),
            nr_password=_env("NR_PASSWORD"),
            nr_schedule_url=_env("NR_SCHEDULE_URL") or NR_SCHEDULE_URL,
            data_dir=Path(_env("TRAINTRACKER_DATA_DIR") or default_data_dir()).expanduser(),
            timetable_max_age_hours=float(_env("TIMETABLE_MAX_AGE_HOURS") or 26),
            min_interchange_minutes=int(_env("MIN_INTERCHANGE_MINUTES") or 5),
            http_timeout=float(_env("HTTP_TIMEOUT_SECONDS") or 15),
        )
