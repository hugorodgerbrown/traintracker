"""The public pages, and that serving them leaves /mcp and /healthz as they were."""

from __future__ import annotations

import asyncio
import html
import json
import re
from collections.abc import Iterator

import pytest
from django.contrib.staticfiles import finders
from django.test import Client
from mcp import Client as MCPClient
from mcp.types import Tool

from apps.public import pages as site
from apps.public import views
from apps.rail import server
from tests.public.fakes import BASE

pytestmark = pytest.mark.django_db

TALLY = '<script async data-domain="traintrackr.live" src="https://fiveb.ar/js/tally.js"></script>'
PROMPTS = (
    "Which platform is the next train from Cambridge to Kings Cross?",
    "What are the next trains from Liverpool Street to Colchester, and are they on time?",
    "How do I get from Cambridge to Sudbury on Saturday morning?",
)
HOME_PROMPTS = (
    "Which platform is the next train from Cambridge to Kings Cross?",
    "Does the 13:42 stop at Manningtree?",
    "How do I get from Cambridge to Huntingdon on Saturday morning?",
)
# The view's own fields, before the fixtures put the fakes' in their place.
REAL_FIELDS = views.fields
STYLESHEET = "/static/css/site.css"
SCRIPT = "/static/js/site.js"
SHARE_IMAGE = "/static/img/share.png"
ICON = "/static/icons/icon.svg"
ICON_PNG = "/static/icons/icon-32.png"
TOUCH_ICON = "/static/icons/icon-180.png"
LISTING_ICON = "/static/icons/icon-512.png"


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The pages' fields as the server's settings make them: 4 at once and 12 a minute."""
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "12")
    monkeypatch.setenv("RATE_LIMIT_BURST", "4")
    REAL_FIELDS.cache_clear()
    monkeypatch.setattr(views, "fields", REAL_FIELDS)
    yield
    REAL_FIELDS.cache_clear()


def _static(url: str) -> bytes:
    """The file a static URL names, as collectstatic gives it to WhiteNoise to serve.

    Args:
        url: The file's address on the site, ``/static/...``.

    Returns:
        The file's bytes.
    """
    found = finders.find(url.removeprefix("/static/"))
    assert isinstance(found, str), url
    with open(found, "rb") as file:
        return file.read()


def _page(client: Client, path: str) -> str:
    """The page's HTML with each run of whitespace as one space.

    So a phrase matches however the source wraps it.

    Args:
        client: The test client.
        path: The page's path.

    Returns:
        The flattened HTML.
    """
    return " ".join(client.get(path).content.decode().split())


def _meta(text: str) -> dict[str, str]:
    """The page's og: and twitter: meta tags, as property or name -> content."""
    pattern = r'<meta (?:property|name)="((?:og|twitter):[^"]+)" content="([^"]*)" ?/?>'
    return dict(re.findall(pattern, text))


def _png_size(data: bytes) -> tuple[int, int]:
    """A PNG's width and height, from its header."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


@pytest.mark.parametrize("path", list(site.PAGES))
def test_pages_are_served(client: Client, path: str) -> None:
    """Each page is whole, filled in, credits the data, and runs nothing inline."""
    r = client.get(path)
    assert r.status_code == 200
    assert r["Content-Type"] == "text/html; charset=utf-8"
    # Nothing inline runs, and nothing from another origin but fivebar's
    # counting script, which sends its counts back to fivebar.
    policy = r["Content-Security-Policy"]
    assert "default-src 'self'" in policy
    assert "unsafe-inline" not in policy
    assert "script-src 'self' https://fiveb.ar;" in policy
    assert "connect-src 'self' https://fiveb.ar;" in policy
    text = r.content.decode()
    assert TALLY in text
    assert r["X-Content-Type-Options"] == "nosniff"
    assert "Set-Cookie" not in r
    assert text.startswith("<!doctype html>")
    assert '<html lang="en-GB">' in text
    assert "{{" not in text, "an unfilled field"
    assert "{%" not in text, "an unfilled tag"
    assert len(re.findall(r"<h1[ >]", text)) == 1
    assert text.count('aria-current="page"') == 1
    assert 'href="#main"' in text
    # Every page credits the live data, links to the other credits and gives
    # the support address.
    assert "Powered by National Rail Enquiries" in text
    assert '<a href="/docs#data">Data sources</a>' in text
    assert "mailto:support@traintrackr.live" in text
    assert client.head(path).status_code == 200


def test_home_is_the_icon_not_a_nav_item(client: Client) -> None:
    """The icon and name at the left go home; the nav lists the other pages."""
    text = client.get("/docs").content.decode()
    home = re.search(r'<a class="name" href="/"[^>]*>(.*?)</a>', text)
    assert home
    assert home.group(1).startswith(f'<img src="{ICON}"')
    nav = text.split("<nav", 1)[1].split("</nav>", 1)[0]
    assert 'href="/"' not in nav
    assert 'aria-current="page"' in client.get("/").content.decode().split("<nav", 1)[0]


@pytest.mark.parametrize("path", list(site.PAGES))
def test_pages_set_the_link_preview_tags(client: Client, path: str) -> None:
    """Chat apps show a wide card, the page's title and its description."""
    text = client.get(path).content.decode()
    meta = _meta(text)
    _, _, title, description = site.PAGES[path]
    assert meta["og:title"] == html.escape(title)
    assert meta["og:description"] == html.escape(description)
    assert meta["og:type"] == "website"
    assert meta["og:site_name"] == "Traintrackr"
    assert meta["twitter:card"] == "summary_large_image"
    assert meta["og:image:alt"] == site.SHARE_IMAGE_ALT
    # Previews are fetched with no page address to resolve against: both are absolute.
    assert meta["og:url"] == f"{BASE}{path}"
    assert meta["og:image"] == f"{BASE}{SHARE_IMAGE}"
    assert all(meta.values()), "an empty tag"
    assert "{{" not in text, "an unfilled field"
    assert "}}" not in text, "an unfilled field"
    image = _static(html.unescape(meta["og:image"]).removeprefix(BASE))
    # The card is the wide shape chat apps show above the title, at the size the tags say.
    assert meta["og:image:type"] == "image/png"
    size = int(meta["og:image:width"]), int(meta["og:image:height"])
    assert size == _png_size(image) == (1200, 630)
    # WhatsApp drops a preview image of more than about 600 kB.
    assert len(image) < 300 * 1024
    # No alpha channel (PNG colour type 2): transparent corners show as white in a chat.
    assert image[25] == 2


def test_the_landing_page_has_the_address_and_the_examples(client: Client) -> None:
    """What it answers, then how to add it to Claude and ChatGPT, with this server's address."""
    text = _page(client, "/")
    # This server's own address, not the one the page was written for.
    assert f'<code id="mcp-url">{BASE}/mcp</code>' in text
    assert 'data-copy="#mcp-url"' in text
    found = [text.index(prompt) for prompt in HOME_PROMPTS]
    assert found == sorted(found), "platforms first, then stops, then journeys"
    # What it answers comes before how to set it up. The address sits in the
    # Claude step that uses it, not above the steps, where it reads as a link.
    assert text.index('id="examples"') < text.index('id="claude"') < text.index('id="url"')
    assert text.index('id="url"') < text.index('id="chatgpt"')
    assert "not a page to open in your browser" in text
    assert "Claude" in text
    assert "ChatGPT" in text
    # ChatGPT adds a server by URL only in developer mode. The page describes
    # how to add it, not where it is listed.
    assert "Developer mode" in text
    assert "directory" not in text
    assert "coming soon" not in text


def test_the_landing_page_shows_an_example_board(client: Client) -> None:
    """Under the lede, a board as a table that site.js draws as flaps."""
    text = _page(client, "/")
    assert text.index('class="lede"') < text.index('class="departures"') < text.index("<h2")
    board = text.split('<figure class="departures">', 1)[1].split("</figure>", 1)[0]
    # The trains are a table in the page: site.js draws the flaps from it, so
    # the page needs no inline script or style, and reads without JavaScript.
    assert "<script" not in board
    assert "style=" not in board
    # The same seven trains as the link-preview card, and the same credit.
    assert board.count("<tr>") == 8
    assert "seven trains" in site.SHARE_IMAGE_ALT
    for cell in ("London Liverpool Street", "Colchester Town", "Cancelled", "Norwich"):
        assert cell in board
    assert "Powered by National Rail Enquiries" in board
    # The times are made up, and the caption says so.
    assert "An example." in board.split("<figcaption>", 1)[1]
    js = _static(SCRIPT).decode()
    assert ".departures" in js
    assert "prefers-reduced-motion" in js


def _tools() -> list[Tool]:
    """The tools the MCP server lists."""

    async def listed() -> list[Tool]:
        """List them through an in-memory MCP client."""
        async with MCPClient(server.mcp) as mcp:
            return (await mcp.list_tools()).tools

    return asyncio.run(listed())


@pytest.mark.usefixtures("configured")
def test_the_docs_cover_every_tool_and_the_limits(client: Client) -> None:
    """Every tool by name and title, the worked examples, the limits and the credits."""
    text = _page(client, "/docs")
    for tool in _tools():
        assert f"<code>{tool.name}</code>" in text, tool.name
        assert tool.title, tool.name
        assert tool.title in text, tool.name
    for prompt in PROMPTS:
        assert prompt in text
    for limit in ("next two hours", "No past running times", "No fares"):
        assert limit in text
    assert "4 requests at once and 12 a minute" in text  # the limits as configured
    assert "a journey plan counts as 3" in text
    # The footer's "Data sources" link lands here, so the full credits must be here.
    assert '<h2 id="data">' in text
    assert "Open Government Licence v3.0" in text
    assert "Open Database License" in text


def test_the_privacy_policy_says_what_the_code_does(client: Client) -> None:
    """Every retention period, processor and right the code relies on is named."""
    text = _page(client, "/privacy")
    for claim in (
        "UK GDPR",
        # Sign-in codes, links and tokens are kept only as hashes (CLAUDE.md, invariant 6).
        "stored as hashes",
        "180 days",  # purge_idle_accounts.IDLE_DAYS
        "15 minutes",  # SIGN_IN_MAX_AGE_SECONDS
        "90 days",  # SESSION_COOKIE_AGE
        "30 days",  # mcp_auth REFRESH_TOKEN_EXPIRE_SECONDS
        "14 days",
        "12 months",
        "IP address",
        "Frankfurt",
        "Resend",
        "Google",
        "Data Privacy Framework",
        "no cookies",
        "fivebar",
        "no advertising",
        "ico.org.uk",
    ):
        assert claim.lower() in text.lower(), claim


@pytest.mark.usefixtures("configured")
def test_the_terms_cover_what_the_directories_ask_for(client: Client) -> None:
    """The warranty, liability, limits, licence and governing law the directories ask for."""
    text = _page(client, "/terms")
    for claim in (
        "provided as it is",
        "Check with the train operator",
        "death or personal injury",
        "can be blocked",
        "4 requests at once and 12 a minute",  # the limits as configured
        "Open Government Licence v3.0",
        'href="/privacy"',
        "England and Wales",
    ):
        assert claim in text, claim


def test_no_page_is_left_with_a_placeholder(client: Client) -> None:
    """The site's TODOs go as each is settled; the logo's waits on National Rail (TRA-9)."""
    for path in site.PAGES:
        text = client.get(path).content.decode()
        todos = re.findall(r"TODO\(hugo\)[^\n]*", text)
        assert all("logo" in t for t in todos), (path, todos)


def test_the_pages_link_the_stylesheet_and_the_script(client: Client) -> None:
    """Each page links site.css and site.js, which collectstatic gives WhiteNoise to serve.

    In production the manifest storage puts each file's hash in its address,
    so the long cache time can't leave a browser with an old stylesheet.
    """
    for path in site.PAGES:
        text = client.get(path).content.decode()
        assert f'<link rel="stylesheet" href="{STYLESHEET}" />' in text, path
        assert f'<script src="{SCRIPT}" defer></script>' in text, path
    assert b"prefers-color-scheme: dark" in _static(STYLESHEET)
    assert _static(SCRIPT)
    assert finders.find("nope.css") is None


def test_the_icon_is_there_at_every_size(client: Client) -> None:
    """The pages link the SVG and PNG icons; the directory listings take the 512 PNG."""
    home = client.get("/").content.decode()
    for path in (ICON, ICON_PNG, TOUCH_ICON):
        assert f'href="{path}"' in home, path
    assert _static(ICON).decode().lstrip().startswith("<svg")
    # The directory listings take a PNG of at least 48 by 48; ChatGPT caps it at 5 MiB.
    for path, size in ((ICON_PNG, 32), (TOUCH_ICON, 180), (LISTING_ICON, 512)):
        assert _png_size(_static(path)) == (size, size), path
    assert len(_static(LISTING_ICON)) < 5 * 1024 * 1024
    # /favicon.ico is the 32 PNG: test_public.test_the_favicon_is_the_png.


def test_the_sign_in_page_uses_the_site_stylesheet(client: Client) -> None:
    """The sign-in page is styled by site.css and the icon, and is not counted."""
    page = client.get("/signin/")
    assert page.status_code == 200
    text = page.content.decode()
    assert f'href="{STYLESHEET}"' in text
    assert "<style" not in text
    assert "style=" not in text
    # A one-time address, not counted.
    assert "fiveb.ar" not in text
    assert f'href="{ICON}"' in text
    assert "unsafe-inline" not in page["Content-Security-Policy"]
    assert page["X-Content-Type-Options"] == "nosniff"


def test_mcp_and_the_health_check_are_unchanged(client: Client) -> None:
    """Beside the pages, /mcp still starts OAuth and /healthz still answers."""
    r = client.post(
        "/mcp",
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}),
        content_type="application/json",
    )
    assert r.status_code == 401
    assert "resource_metadata" in r["WWW-Authenticate"]
    assert client.get("/healthz").content == b"ok"
