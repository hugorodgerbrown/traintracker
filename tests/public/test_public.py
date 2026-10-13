"""What Django adds around the pages: headers, framing, caching and limits."""

import re
from typing import Any

import pytest
from django.test import Client

from apps.public import boards
from tests.traintracker.test_boards import FakeFetch
from traintracker.site import boards as tt

pytestmark = pytest.mark.django_db


def test_pages_are_cached_briefly_and_never_framed(client: Client) -> None:
    """A page may be kept five minutes; no other site may put it in a frame."""
    response = client.get("/docs")
    assert response["Cache-Control"] == "public, max-age=300"
    assert response["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in response["Content-Security-Policy"]


def test_pages_count_visits_only_while_fivebar_is_on(client: Client, settings: Any) -> None:
    """The tag is there with FIVEBAR_DOMAIN (production sets traintrackr.live), else not."""
    tag = '<script async data-domain="traintrackr.live" src="https://fiveb.ar/js/tally.js">'
    assert tag in client.get("/").content.decode()
    settings.FIVEBAR_DOMAIN = ""
    assert "fiveb.ar" not in client.get("/").content.decode()


def test_only_an_embedded_board_may_be_framed(client: Client) -> None:
    """?embed=1 is the one page another site may frame."""
    embedded = client.get("/board/LST?embed=1")
    assert "X-Frame-Options" not in embedded
    assert "frame-ancestors *" in embedded["Content-Security-Policy"]
    for address in ("/board/LST", "/board/LST?tv=1", "/board"):
        response = client.get(address)
        assert response["X-Frame-Options"] == "DENY", address
        assert "frame-ancestors 'none'" in response["Content-Security-Policy"], address


def test_every_link_is_a_path_on_the_site(client: Client) -> None:
    """Links the views fill in start with "/"; an embed's link opens the board on the site."""
    for address in ("/", "/board/LST", "/board/LST/COL", "/board?station=liverpool", "/board"):
        hrefs = re.findall(r'<a [^>]*href="([^"]*)"', client.get(address).content.decode())
        assert hrefs, address
        assert all(h.startswith(("/", "#", "https://", "mailto:")) for h in hrefs), address
    embedded = client.get("/board/LST?embed=1").content.decode()
    assert '<a href="/board/LST" target="_blank" rel="noopener">Traintrackr</a>' in embedded


def test_boards_are_cached_for_half_a_minute_and_the_feed_to_its_minute(client: Client) -> None:
    """A board page for 30 seconds; its data no later than the minute it is for."""
    assert client.get("/board/LST")["Cache-Control"] == "public, max-age=30"
    max_age = int(client.get("/api/board/LST")["Cache-Control"].split("=")[1])
    assert 0 <= max_age <= 60


def test_every_viewer_shares_one_fetch(client: Client, fetch: FakeFetch) -> None:
    """However many pages show a board, Darwin is asked once a minute."""
    for _ in range(3):
        client.get("/board/LST")
        client.get("/api/board/LST")
    assert fetch.calls == [("LST", None, None)]


def test_one_address_can_open_boards_only_so_fast(client: Client) -> None:
    """Past the limit an address waits, told how long; nobody else is affected."""
    for _ in range(boards.BURST):
        assert client.get("/board/LST").status_code == 200
    page = client.get("/board/LST")
    assert page.status_code == 429
    assert 1 <= int(page["Retry-After"]) <= boards.WINDOW
    assert page["Cache-Control"] == "private, no-store"
    data = client.get("/api/board/LST")
    assert data.status_code == 429
    assert "Try again in" in data.json()["error"]
    other = client.get("/board/LST", REMOTE_ADDR="192.0.2.9")
    assert other.status_code == 200


def test_the_favicon_is_the_png(client: Client) -> None:
    """Browsers ask for /favicon.ico whatever the page links to."""
    response = client.get("/favicon.ico")
    assert response.status_code == 301
    assert response["Location"].endswith("icon-32.png")


def test_the_openai_challenge(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    """Only once OPENAI_APPS_CHALLENGE is set, and then as bare text."""
    monkeypatch.delenv("OPENAI_APPS_CHALLENGE", raising=False)
    monkeypatch.setenv("TRAINTRACKER_NO_DOTENV", "1")
    assert client.get("/.well-known/openai-apps-challenge").status_code == 404
    monkeypatch.setenv("OPENAI_APPS_CHALLENGE", "token-123")
    response = client.get("/.well-known/openai-apps-challenge")
    assert response.content == b"token-123"
    assert response["Content-Type"].startswith("text/plain")


def test_the_shared_boards_fetch_through_the_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """Outside the tests, a board comes from the server's own public_board."""
    monkeypatch.undo()
    boards.shared.cache_clear()
    from traintracker import server

    assert isinstance(boards.shared(), tt.SharedBoards)
    assert boards.shared().fetch is server.public_board
    boards.shared.cache_clear()
