"""Tests for the rail management commands: refresh_timetable, import_timetable, rail_status."""

import gzip
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import CommandError, call_command

from tests.traintracker import feedgen
from traintracker.config import Settings
from traintracker.timetable import Timetable, TimetableMissing


def run(name: str, *args: str | Path) -> str:
    """Run a command and return what it wrote."""
    out = StringIO()
    call_command(name, *args, stdout=out)
    return out.getvalue()


@pytest.fixture
def feed_file(tmp_path: Path) -> Path:
    """A small SCHEDULE extract, gzipped as Network Rail serves it."""
    path = tmp_path / "schedule.json.gz"
    path.write_bytes(gzip.compress("\n".join(feedgen.feed()).encode()))
    return path


def test_import_is_a_dry_run_without_commit(feed_file: Path) -> None:
    """Without --commit the timetable is left as it was."""
    assert "pass --commit" in run("import_timetable", feed_file)
    with pytest.raises(TimetableMissing):
        Timetable.open(Settings.from_env().timetable_db)


def test_import_builds_the_timetable(feed_file: Path) -> None:
    """--commit builds the schema from the file."""
    out = run("import_timetable", feed_file, "--commit")
    assert "Timetable built in schema" in out
    assert Timetable.open(Settings.from_env().timetable_db).meta["built_at"]


def test_import_refuses_a_missing_file(tmp_path: Path) -> None:
    """A path that isn't a file is an error, not a traceback."""
    with pytest.raises(CommandError, match="No such file"):
        run("import_timetable", tmp_path / "nope.json.gz", "--commit")


def test_refresh_is_a_dry_run_without_commit() -> None:
    """Without --commit nothing is downloaded."""
    assert "Network Rail's daily extract (pass --commit)" in run("refresh_timetable")


def test_refresh_without_network_rail_account_fails_cleanly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no NR account the command says what to set and exits non-zero."""
    monkeypatch.delenv("NR_USERNAME", raising=False)
    monkeypatch.delenv("NR_PASSWORD", raising=False)
    with pytest.raises(CommandError, match="NR_USERNAME"):
        run("refresh_timetable", "--commit")


def test_refresh_in_demo_mode_builds_the_generated_timetable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Demo mode rebuilds from the generated feed, with no account."""
    monkeypatch.setenv("TRAINTRACKER_DEMO", "1")
    monkeypatch.setenv("TRAINTRACKER_DATA_DIR", str(tmp_path))
    assert "generated demo feed" in run("refresh_timetable")
    assert "Timetable built in schema" in run("refresh_timetable", "--commit")


def test_status_reports_sources_and_timetable(feed_file: Path) -> None:
    """rail_status says what is configured and when the timetable was built."""
    run("import_timetable", feed_file, "--commit")
    out = run("rail_status")
    assert "Darwin:" in out
    assert "built_at" in out
