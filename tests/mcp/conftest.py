"""Fixtures for the MCP endpoint's tests: the train tools on demo data."""

import os
import uuid
from collections.abc import Iterator

import psycopg
import pytest

from apps.rail import runtime
from apps.rail.timetable import Timetable
from tests.rail.conftest import TEST_DATABASE_URL


@pytest.fixture(scope="module")
def train_tools() -> Iterator[None]:
    """Start the train tools in demo mode for a module's tests, then stop them.

    Module-scoped because the demo timetable takes a few seconds to build.
    The schemas are this module's own and are dropped afterwards.
    """
    base = f"test_{uuid.uuid4().hex[:12]}"
    env = {
        "TRAINTRACKER_NO_DOTENV": "1",
        "TRAINTRACKER_DEMO": "1",
        "DATABASE_URL": TEST_DATABASE_URL,
        "TIMETABLE_SCHEMA": base,
        "USAGE_SCHEMA": f"{base}_usage",
    }
    saved = {name: os.environ.get(name) for name in env}
    os.environ.update(env)
    try:
        yield
    finally:
        runtime.stop()
        Timetable.clear_caches()
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as con:
            for (name,) in con.execute(
                "SELECT nspname FROM pg_namespace WHERE nspname LIKE %s", (f"{base}%",)
            ).fetchall():
                con.execute(
                    psycopg.sql.SQL("DROP SCHEMA {} CASCADE").format(psycopg.sql.Identifier(name))
                )
