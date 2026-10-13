"""Downloading Network Rail's SCHEDULE extract and building the timetable from it."""

from __future__ import annotations

import gzip
import logging
from io import StringIO
from pathlib import Path

import httpx
import pytest
import respx
from django.core.management import call_command

from apps.rail.config import NR_SCHEDULE_URL, Settings
from apps.rail.errors import NotConfigured, UpstreamError
from apps.rail.timetable import Timetable, TimetableMissing, download_and_build

from . import feedgen


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Settings with a Network Rail account."""
    monkeypatch.setenv("NR_USERNAME", "me@example.com")
    monkeypatch.setenv("NR_PASSWORD", "secret")
    monkeypatch.setenv("TRAINTRACKER_DATA_DIR", str(tmp_path))
    return Settings.from_env()


@respx.mock
async def test_download_follows_redirect_and_builds(settings: Settings) -> None:
    """The download follows the redirect to S3 and builds the timetable."""
    body = gzip.compress("\n".join(feedgen.feed()).encode())
    first = respx.get(NR_SCHEDULE_URL).mock(
        return_value=httpx.Response(302, headers={"Location": "https://s3.example/feed.gz"})
    )
    respx.get("https://s3.example/feed.gz").mock(
        return_value=httpx.Response(200, content=body, headers={"content-type": "application/gzip"})
    )
    meta = await download_and_build(settings)
    assert first.calls.last.request.headers["Authorization"].startswith("Basic ")
    assert int(meta["public_schedules"]) > 0
    assert Timetable.open(settings.timetable_db).meta["built_at"] == meta["built_at"]
    assert not list(settings.data_dir.glob("*.part"))  # temp download removed


@respx.mock
async def test_login_page_is_reported(settings: Settings) -> None:
    """A sign-in page in place of the feed is an error, and builds nothing."""
    respx.get(NR_SCHEDULE_URL).mock(
        return_value=httpx.Response(
            200, text="<html>Sign in</html>", headers={"content-type": "text/html"}
        )
    )
    with pytest.raises(UpstreamError, match="web page"):
        await download_and_build(settings)
    with pytest.raises(TimetableMissing):
        Timetable.open(settings.timetable_db)


@respx.mock
async def test_bad_credentials(settings: Settings) -> None:
    """Rejected credentials are reported as such."""
    respx.get(NR_SCHEDULE_URL).mock(return_value=httpx.Response(401))
    with pytest.raises(UpstreamError, match="rejected"):
        await download_and_build(settings)


@respx.mock
async def test_corrupt_download(settings: Settings) -> None:
    """A download that isn't gzip is reported as unreadable."""
    respx.get(NR_SCHEDULE_URL).mock(
        return_value=httpx.Response(200, content=b"\x1f\x8b broken gzip")
    )
    with pytest.raises(UpstreamError, match="couldn't be read"):
        await download_and_build(settings)


async def test_needs_credentials(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without an account the error says where to get one."""
    monkeypatch.delenv("NR_PASSWORD")
    with pytest.raises(NotConfigured, match="publicdatafeeds"):
        await download_and_build(Settings.from_env())


@respx.mock
def test_refresh_does_not_log_presigned_url(
    settings: Settings,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """refresh_timetable never logs the presigned S3 URL."""
    # Django's LOGGING holds httpx and httpcore at WARNING, below what the
    # test captures.
    caplog.set_level(logging.DEBUG)
    presigned = (
        "https://ntrod-prod2-schedule-json.s3.eu-west-2.amazonaws.com/feed.gz"
        "?X-Amz-Credential=AKIA%2Fexample&X-Amz-Security-Token=token&X-Amz-Signature=sig"
    )
    respx.get(NR_SCHEDULE_URL).mock(
        return_value=httpx.Response(302, headers={"Location": presigned})
    )
    respx.get(presigned).mock(
        return_value=httpx.Response(
            200,
            content=gzip.compress("\n".join(feedgen.feed()).encode()),
            headers={"content-type": "application/gzip"},
        )
    )
    out = StringIO()
    call_command("refresh_timetable", "--commit", stdout=out)
    assert "Timetable built in schema" in out.getvalue()
    assert Timetable.open(settings.timetable_db).meta
    assert "X-Amz-" not in caplog.text
    assert "X-Amz-" not in capsys.readouterr().err
