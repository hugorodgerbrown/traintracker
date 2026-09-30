"""Configuration from environment variables. Nothing is read from disk."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from zoneinfo import ZoneInfo

UK_TZ = ZoneInfo("Europe/London")

# Rail Data Marketplace product base URLs. Each RDM product has its own URL and
# its own consumer key; copy both from the product's "Specification" tab.
DARWIN_DEPARTURES_URL = (
    "https://api1.raildata.org.uk/1010-live-departure-board-dep1_2/LDBWS/api/20220120"
)
DARWIN_SERVICE_URL = "https://api1.raildata.org.uk/1010-service-details1_2/LDBWS/api/20220120"
# Demo mode serves Darwin from generated data in-process; this host never resolves.
DEMO_DARWIN_URL = "https://demo.traintracker.invalid/LDBWS"
NR_SCHEDULE_URL = (
    "https://publicdatafeeds.networkrail.co.uk/ntrod/CifFileAuthenticate"
    "?type=CIF_ALL_FULL_DAILY&day=toc-full"
)


TRUE = {"1", "true", "yes", "on"}


def _public_hosts() -> tuple[str, ...]:
    """Hostnames clients use: MCP_PUBLIC_HOSTS (e.g. a custom domain) plus the
    service's own Render hostname (RENDER_EXTERNAL_HOSTNAME), which is always kept
    so every deployment answers on its onrender.com name."""
    names = [*(_env("MCP_PUBLIC_HOSTS") or "").split(","), _env("RENDER_EXTERNAL_HOSTNAME") or ""]
    return tuple(dict.fromkeys(h.strip() for h in names if h.strip()))


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


SCHEMA_NAME = re.compile(r"[a-z_][a-z0-9_]{0,40}")


@dataclass(frozen=True)
class TimetableDB:
    """Where a timetable lives: a Postgres database and the schema inside it."""

    dsn: str = field(repr=False)
    schema: str

    def __post_init__(self) -> None:
        if not SCHEMA_NAME.fullmatch(self.schema):
            raise ValueError(f"Invalid timetable schema name {self.schema!r}.")


@dataclass(frozen=True)
class Settings:
    darwin_key: str | None = field(repr=False)
    darwin_departures_url: str
    darwin_service_key: str | None = field(repr=False)
    darwin_service_url: str
    darwin_arrivals_key: str | None = field(repr=False)
    darwin_arrivals_url: str | None
    nr_username: str | None
    nr_password: str | None = field(repr=False)
    nr_schedule_url: str
    database_url: str | None = field(repr=False)
    timetable_schema: str
    auto_refresh: bool
    mcp_auth_token: str | None = field(repr=False)
    host: str
    port: int
    public_hosts: tuple[str, ...]
    public_url: str
    oauth_passphrase: str | None = field(repr=False)
    auth_schema: str
    data_dir: Path
    timetable_max_age_hours: float
    min_interchange_minutes: int
    http_timeout: float
    demo: bool = False
    resend_api_key: str | None = field(default=None, repr=False)
    mail_from: str | None = None
    account_secret: str | None = field(default=None, repr=False)
    mail_backend: str = "resend"
    mail_max_per_hour: int = 200
    rate_limit_per_minute: int = 30
    rate_limit_burst: int = 10
    usage_schema: str = "traintracker_usage"
    openai_apps_challenge: str | None = None

    @property
    def timetable_db(self) -> TimetableDB:
        # A separate schema, so demo data never mixes with the real timetable.
        schema = f"{self.timetable_schema}_demo" if self.demo else self.timetable_schema
        return TimetableDB(self.database_url or "", schema)

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
    def has_any_darwin(self) -> bool:
        """Whether any Darwin product can be called: each has its own key, and a
        server may hold the arrivals or service-details one without departures."""
        return self.has_darwin or self.has_darwin_arrivals or self.darwin_service_key is not None

    @property
    def email_sign_in_missing(self) -> list[str]:
        """Variables still needed for email sign-in, once any part of it is set.

        Empty when email sign-in is either complete or not asked for, so a
        half-configured server is refused at start-up rather than found out by
        the first person who tries to sign in.
        """
        console = self.mail_backend == "console"
        if not (console or self.resend_api_key or self.mail_from or self.account_secret):
            return []
        needed = {"MCP_ACCOUNT_SECRET": self.account_secret}
        if not console:
            needed |= {"RESEND_API_KEY": self.resend_api_key, "MAIL_FROM": self.mail_from}
        return [name for name, value in needed.items() if not value]

    @property
    def email_sign_in(self) -> bool:
        """Whether people can sign in with a code sent to their email address."""
        console = self.mail_backend == "console"
        asked = console or bool(self.resend_api_key or self.mail_from or self.account_secret)
        return asked and not self.email_sign_in_missing

    @classmethod
    def from_env(cls) -> Settings:
        darwin_key = _env("DARWIN_API_KEY")
        port = int(_env("PORT") or 8000)
        public_hosts = _public_hosts()
        # The URL clients reach /mcp under; OAuth issuer and resource derive from it.
        public_url = (_env("MCP_PUBLIC_URL") or "").rstrip("/") or (
            f"https://{public_hosts[0]}" if public_hosts else f"http://localhost:{port}"
        )
        settings = cls(
            darwin_key=darwin_key,
            darwin_departures_url=_env("DARWIN_DEPARTURES_URL") or DARWIN_DEPARTURES_URL,
            # Service details is a separate RDM product; fall back to the board key.
            darwin_service_key=_env("DARWIN_SERVICE_API_KEY") or darwin_key,
            darwin_service_url=_env("DARWIN_SERVICE_URL") or DARWIN_SERVICE_URL,
            darwin_arrivals_key=_env("DARWIN_ARRIVALS_API_KEY"),
            darwin_arrivals_url=_env("DARWIN_ARRIVALS_URL"),
            nr_username=_env("NR_USERNAME"),
            nr_password=_env("NR_PASSWORD"),
            nr_schedule_url=_env("NR_SCHEDULE_URL") or NR_SCHEDULE_URL,
            database_url=_env("DATABASE_URL"),
            timetable_schema=_env("TIMETABLE_SCHEMA") or "timetable",
            # Off where a separate cron job runs `traintracker refresh`.
            auto_refresh=(_env("TIMETABLE_AUTO_REFRESH") or "1").lower() in TRUE,
            mcp_auth_token=_env("MCP_AUTH_TOKEN"),
            host=_env("HOST") or "0.0.0.0",  # all interfaces: the HTTP server is for hosting
            port=port,
            public_hosts=public_hosts,
            public_url=public_url,
            oauth_passphrase=_env("MCP_OAUTH_PASSPHRASE"),
            auth_schema=_env("MCP_AUTH_SCHEMA") or "mcp_auth",
            data_dir=Path(_env("TRAINTRACKER_DATA_DIR") or default_data_dir()).expanduser(),
            timetable_max_age_hours=float(_env("TIMETABLE_MAX_AGE_HOURS") or 26),
            min_interchange_minutes=int(_env("MIN_INTERCHANGE_MINUTES") or 5),
            http_timeout=float(_env("HTTP_TIMEOUT_SECONDS") or 15),
            resend_api_key=_env("RESEND_API_KEY"),
            mail_from=_env("MAIL_FROM"),
            account_secret=_env("MCP_ACCOUNT_SECRET"),
            mail_backend=(_env("MAIL_BACKEND") or "resend").lower(),
            mail_max_per_hour=int(_env("MAIL_MAX_PER_HOUR") or 200),
            rate_limit_per_minute=int(_env("RATE_LIMIT_PER_MINUTE") or 30),
            rate_limit_burst=int(_env("RATE_LIMIT_BURST") or 10),
            usage_schema=_env("USAGE_SCHEMA") or "traintracker_usage",
            openai_apps_challenge=_env("OPENAI_APPS_CHALLENGE"),
        )
        if (_env("TRAINTRACKER_DEMO") or "").lower() not in TRUE:
            return settings
        # Generated data only: every Darwin product on, no real account used.
        return replace(
            settings,
            darwin_key="demo",
            darwin_departures_url=DEMO_DARWIN_URL,
            darwin_service_key="demo",
            darwin_service_url=DEMO_DARWIN_URL,
            darwin_arrivals_key="demo",
            darwin_arrivals_url=DEMO_DARWIN_URL,
            nr_username=None,
            nr_password=None,
            demo=True,
        )
