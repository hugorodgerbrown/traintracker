from __future__ import annotations

import gzip
from pathlib import Path

import httpx
import pytest
import respx

from traintracker.config import NR_SCHEDULE_URL, Settings
from traintracker.errors import NotConfigured, UpstreamError
from traintracker.timetable import Timetable, download_and_build

from . import feedgen


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("NR_USERNAME", "me@example.com")
    monkeypatch.setenv("NR_PASSWORD", "secret")
    monkeypatch.setenv("TRAINTRACKER_DATA_DIR", str(tmp_path))
    return Settings.from_env()


@respx.mock
async def test_download_follows_redirect_and_builds(settings: Settings) -> None:
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
    assert Timetable.open(settings.timetable_path).meta["built_at"] == meta["built_at"]
    assert not list(settings.data_dir.glob("*.part"))  # temp download removed


@respx.mock
async def test_login_page_is_reported(settings: Settings) -> None:
    respx.get(NR_SCHEDULE_URL).mock(
        return_value=httpx.Response(
            200, text="<html>Sign in</html>", headers={"content-type": "text/html"}
        )
    )
    with pytest.raises(UpstreamError, match="web page"):
        await download_and_build(settings)
    assert not settings.timetable_path.exists()


@respx.mock
async def test_bad_credentials(settings: Settings) -> None:
    respx.get(NR_SCHEDULE_URL).mock(return_value=httpx.Response(401))
    with pytest.raises(UpstreamError, match="rejected"):
        await download_and_build(settings)


@respx.mock
async def test_corrupt_download(settings: Settings) -> None:
    respx.get(NR_SCHEDULE_URL).mock(
        return_value=httpx.Response(200, content=b"\x1f\x8b broken gzip")
    )
    with pytest.raises(UpstreamError, match="couldn't be read"):
        await download_and_build(settings)


async def test_needs_credentials(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NR_PASSWORD")
    with pytest.raises(NotConfigured, match="publicdatafeeds"):
        await download_and_build(Settings.from_env())
