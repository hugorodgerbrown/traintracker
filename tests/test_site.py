"""The public pages, and that serving them leaves /mcp and /healthz as they were."""

from __future__ import annotations

import html
import os
import re
from collections.abc import AsyncIterator

import httpx
import pytest
from mcp import Client

from traintracker import server, site
from traintracker.config import Settings
from traintracker.http_app import build_app

BASE = "https://tt.test"
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


@pytest.fixture
async def client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[httpx.AsyncClient]:
    monkeypatch.setenv("MCP_PUBLIC_URL", BASE)
    monkeypatch.setenv("MCP_PUBLIC_HOSTS", "tt.test")
    monkeypatch.setenv("MCP_OAUTH_PASSPHRASE", "correct horse battery staple")
    monkeypatch.setenv("MCP_AUTH_SCHEMA", os.environ["TIMETABLE_SCHEMA"] + "_auth")
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "12")
    monkeypatch.setenv("RATE_LIMIT_BURST", "4")
    transport = httpx.ASGITransport(app=build_app(server.mcp, Settings.from_env()))
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as c:
        yield c


@pytest.mark.parametrize("path", list(site.PAGES))
async def test_pages_are_served(client: httpx.AsyncClient, path: str) -> None:
    r = await client.get(path)
    assert r.status_code == 200
    assert r.headers["content-type"] == "text/html; charset=utf-8"
    # Nothing inline and nothing from another origin can run or load.
    policy = r.headers["content-security-policy"]
    assert "default-src 'none'" in policy and "unsafe-inline" not in policy
    assert r.headers["strict-transport-security"] == "max-age=31536000"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "set-cookie" not in r.headers
    assert r.text.startswith("<!doctype html>") and '<html lang="en-GB">' in r.text
    assert "{{" not in r.text, "an unfilled field"
    assert len(re.findall(r"<h1[ >]", r.text)) == 1
    assert r.text.count('aria-current="page"') == 1 and 'href="#main"' in r.text
    # Every page credits the live data, links to the other credits and gives
    # the support address.
    assert "Powered by National Rail Enquiries" in r.text
    assert '<a href="/docs#data">Data sources</a>' in r.text
    assert "mailto:support@traintrackr.live" in r.text
    assert (await client.head(path)).status_code == 200


async def test_home_is_the_icon_not_a_nav_item(client: httpx.AsyncClient) -> None:
    r = await client.get("/docs")
    home = re.search(r'<a class="name" href="/"[^>]*>(.*?)</a>', r.text)
    assert home and home.group(1).startswith('<img src="/static/icon.svg?v=')
    nav = r.text.split("<nav", 1)[1].split("</nav>", 1)[0]
    assert 'href="/"' not in nav
    assert 'aria-current="page"' in (await client.get("/")).text.split("<nav", 1)[0]


async def _page(client: httpx.AsyncClient, path: str) -> str:
    """The page's HTML with each run of whitespace as one space, so a phrase
    matches however the source wraps it."""
    return " ".join((await client.get(path)).text.split())


def _meta(text: str) -> dict[str, str]:
    """The page's og: and twitter: meta tags, as property or name -> content."""
    pattern = r'<meta (?:property|name)="((?:og|twitter):[^"]+)" content="([^"]*)" ?/?>'
    return dict(re.findall(pattern, text))


@pytest.mark.parametrize("path", list(site.PAGES))
async def test_pages_set_the_link_preview_tags(client: httpx.AsyncClient, path: str) -> None:
    text = (await client.get(path)).text
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
    assert meta["og:image"].startswith(f"{BASE}{site.SHARE_IMAGE}?v=")
    assert all(meta.values()), "an empty tag"
    assert "{{" not in text and "}}" not in text, "an unfilled field"
    image = await client.get(html.unescape(meta["og:image"]).removeprefix(BASE))
    assert image.status_code == 200 and image.headers["content-type"] == "image/png"
    # The card is the wide shape chat apps show above the title, at the size the tags say.
    assert meta["og:image:type"] == "image/png"
    size = int(meta["og:image:width"]), int(meta["og:image:height"])
    assert size == _png_size(image.content) == (1200, 630)
    # WhatsApp drops a preview image of more than about 600 kB.
    assert len(image.content) < 300 * 1024
    # No alpha channel (PNG colour type 2): transparent corners show as white in a chat.
    assert image.content[25] == 2


async def test_the_landing_page_has_the_address_and_the_examples(
    client: httpx.AsyncClient,
) -> None:
    text = await _page(client, "/")
    # This server's own address, not the one the page was written for.
    assert f'<code id="mcp-url">{BASE}/mcp</code>' in text
    assert 'data-copy="#mcp-url"' in text
    found = [text.index(prompt) for prompt in HOME_PROMPTS]
    assert found == sorted(found), "platforms first, then stops, then journeys"
    # What it answers comes before how to set it up.
    assert text.index('id="examples"') < text.index('id="url"') < text.index('id="claude"')
    assert "Claude" in text and "ChatGPT" in text
    # ChatGPT can't add a server outside its directory without developer mode.
    assert "Developer mode" in text and "isn't in the ChatGPT directory yet" in text


async def test_the_docs_cover_every_tool_and_the_limits(client: httpx.AsyncClient) -> None:
    text = await _page(client, "/docs")
    async with Client(server.mcp) as mcp:
        tools = (await mcp.list_tools()).tools
    for tool in tools:
        assert f"<code>{tool.name}</code>" in text, tool.name
        assert tool.title and tool.title in text, tool.name
    for prompt in PROMPTS:
        assert prompt in text
    for limit in ("next two hours", "No past running times", "No fares"):
        assert limit in text
    assert "4 requests at once and 12 a minute" in text  # the limits as configured
    assert "a journey plan counts as 3" in text
    # The footer's "Data sources" link lands here, so the full credits must be here.
    assert '<h2 id="data">' in text
    assert "Open Government Licence v3.0" in text and "Open Database License" in text


async def test_the_privacy_policy_says_what_the_code_does(client: httpx.AsyncClient) -> None:
    text = await _page(client, "/privacy")
    for claim in (
        "UK GDPR",
        "keyed hash",
        "180 days",
        "24 hours",
        "90 days",
        "14 days",
        "12 months",
        "IP address",
        "Frankfurt",
        "Resend",
        "Google",
        "Data Privacy Framework",
        "no cookies",
        "no analytics",
        "ico.org.uk",
    ):
        assert claim.lower() in text.lower(), claim


async def test_the_terms_cover_what_the_directories_ask_for(client: httpx.AsyncClient) -> None:
    text = await _page(client, "/terms")
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


async def test_no_page_is_left_with_a_placeholder(client: httpx.AsyncClient) -> None:
    # The site's own TODO comments are removed as each item is settled; the logo
    # one stays until National Rail answers (TRA-9).
    for path in site.PAGES:
        text = (await client.get(path)).text
        todos = re.findall(r"TODO\(hugo\)[^\n]*", text)
        assert all("logo" in t for t in todos), (path, todos)


async def test_assets_are_served(client: httpx.AsyncClient) -> None:
    # Pages link to an address that changes with the file, so the long cache
    # time can't leave a browser with an old stylesheet.
    home = (await client.get("/")).text
    assert re.fullmatch(r"/static/site\.css\?v=[0-9a-f]{10}", site.asset_url(site.STYLESHEET))
    assert f'href="{site.asset_url(site.STYLESHEET)}"' in home
    assert f'src="{site.asset_url(site.SCRIPT)}"' in home
    css = await client.get(site.asset_url(site.STYLESHEET))
    assert css.status_code == 200 and css.headers["content-type"] == "text/css; charset=utf-8"
    assert "prefers-color-scheme: dark" in css.text
    js = await client.get(site.SCRIPT)
    assert js.status_code == 200
    assert js.headers["content-type"] == "text/javascript; charset=utf-8"
    assert (await client.get("/static/nope.css")).status_code == 404


def _png_size(data: bytes) -> tuple[int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


async def test_the_icon_is_served_at_every_size(client: httpx.AsyncClient) -> None:
    home = (await client.get("/")).text
    for path in (site.ICON, site.ICON_PNG, site.TOUCH_ICON):
        assert f'href="{site.asset_url(path)}"' in home, path
    svg = await client.get(site.asset_url(site.ICON))
    assert svg.status_code == 200 and svg.headers["content-type"] == "image/svg+xml"
    assert svg.text.lstrip().startswith("<svg")
    # The directory listings take a PNG of at least 48 by 48; ChatGPT caps it at 5 MiB.
    for path, size in (
        (site.ICON_PNG, 32),
        (site.TOUCH_ICON, 180),
        (site.LISTING_ICON, 512),
        (site.FAVICON, 32),
    ):
        r = await client.get(path)
        assert r.status_code == 200 and r.headers["content-type"] == "image/png", path
        assert _png_size(r.content) == (size, size), path
    assert len((await client.get(site.LISTING_ICON)).content) < 5 * 1024 * 1024


async def test_the_sign_in_page_uses_the_site_stylesheet(client: httpx.AsyncClient) -> None:
    page = await client.get("/sign-in", params={"request": "made-up"})
    assert f'href="{site.asset_url(site.STYLESHEET)}"' in page.text
    assert "<style" not in page.text
    assert f'href="{site.asset_url(site.ICON)}"' in page.text
    assert page.headers["content-security-policy"] == (
        "default-src 'none'; style-src 'self'; img-src 'self'"
    )
    assert page.headers["strict-transport-security"] == "max-age=31536000"
    assert page.headers["x-content-type-options"] == "nosniff"


async def test_mcp_and_the_health_check_are_unchanged(client: httpx.AsyncClient) -> None:
    r = await client.post("/mcp", json={})
    assert r.status_code == 401
    assert "resource_metadata" in r.headers["www-authenticate"]
    assert (await client.get("/healthz")).text == "ok"
