from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import psycopg
import pytest

from traintracker.config import TimetableDB
from traintracker.timetable import Timetable

# Tests need a Postgres they can create schemas in. CI provides one as a service;
# locally, see "Development" in the README. The default port is not 5432, so a
# test run never lands in another project's local database.
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:55432/postgres"
)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # Never pick up a developer's real .env (it could trigger a live download).
    monkeypatch.setenv("TRAINTRACKER_NO_DOTENV", "1")
    # Each test gets its own schemas, dropped afterwards.
    base = f"test_{uuid.uuid4().hex[:12]}"
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("TIMETABLE_SCHEMA", base)
    monkeypatch.setenv("USAGE_SCHEMA", f"{base}_usage")
    yield
    Timetable.clear_caches()
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as con:
        schemas = con.execute(
            "SELECT nspname FROM pg_namespace WHERE nspname LIKE %s", (f"{base}%",)
        ).fetchall()
        for (name,) in schemas:
            con.execute(
                psycopg.sql.SQL("DROP SCHEMA {} CASCADE").format(psycopg.sql.Identifier(name))
            )


def timetable_db(suffix: str = "") -> TimetableDB:
    """A timetable location for this test; `suffix` gives a test several of them."""
    return TimetableDB(os.environ["DATABASE_URL"], os.environ["TIMETABLE_SCHEMA"] + suffix)
