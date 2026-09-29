"""The public pages, and that serving them leaves /mcp and /healthz as they were."""

from __future__ import annotations

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
    assert "set-cookie" not in r.headers
    assert r.text.startswith("<!doctype html>") and '<html lang="en-GB">' in r.text
    assert "{{" not in r.text, "an unfilled field"
    assert len(re.findall(r"<h1[ >]", r.text)) == 1
    assert 'aria-current="page"' in r.text and 'href="#main"' in r.text
    # Every page credits the data and gives the support address.
    assert "Powered by National Rail Enquiries" in r.text
    assert "Open Government Licence v3.0" in r.text and "Open Database License" in r.text
    assert "mailto:support@traintrackr.live" in r.text
    assert (await client.head(path)).status_code == 200


async def test_the_landing_page_has_the_address_and_the_examples(
    client: httpx.AsyncClient,
) -> None:
    text = (await client.get("/")).text
    # This server's own address, not the one the page was written for.
    assert f'<code id="mcp-url">{BASE}/mcp</code>' in text
    assert 'data-copy="#mcp-url"' in text
    found = [text.index(prompt) for prompt in PROMPTS]
    assert found == sorted(found), "platforms first, then departures, then journeys"
    assert "Claude" in text and "ChatGPT" in text


async def test_the_docs_cover_every_tool_and_the_limits(client: httpx.AsyncClient) -> None:
    text = (await client.get("/docs")).text
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


async def test_the_privacy_policy_says_what_the_code_does(client: httpx.AsyncClient) -> None:
    text = (await client.get("/privacy")).text
    for claim in (
        "UK GDPR",
        "keyed hash",
        "180 days",
        "24 hours",
        "90 days",
        "IP address",
        "Frankfurt",
        "Resend",
        "no cookies",
        "no analytics",
        "ico.org.uk",
    ):
        assert claim.lower() in text.lower(), claim


async def test_assets_are_served(client: httpx.AsyncClient) -> None:
    css = await client.get(site.STYLESHEET)
    assert css.status_code == 200 and css.headers["content-type"] == "text/css; charset=utf-8"
    assert "prefers-color-scheme: dark" in css.text
    js = await client.get(site.SCRIPT)
    assert js.status_code == 200
    assert js.headers["content-type"] == "text/javascript; charset=utf-8"
    assert (await client.get("/static/nope.css")).status_code == 404


async def test_the_sign_in_page_uses_the_site_stylesheet(client: httpx.AsyncClient) -> None:
    page = await client.get("/sign-in", params={"request": "made-up"})
    assert f'href="{site.STYLESHEET}"' in page.text and "<style" not in page.text
    assert page.headers["content-security-policy"] == "default-src 'none'; style-src 'self'"


async def test_mcp_and_the_health_check_are_unchanged(client: httpx.AsyncClient) -> None:
    r = await client.post("/mcp", json={})
    assert r.status_code == 401
    assert "resource_metadata" in r.headers["www-authenticate"]
    assert (await client.get("/healthz")).text == "ok"
