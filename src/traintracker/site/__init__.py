"""The public site: what the server is, how to use it, and the privacy policy.

Directory reviewers, and anyone handed the connector URL, need somewhere to
read about it, so the pages are served by the same app as /mcp: one deploy,
one domain. They are plain files next to this module, with no build step. Each
page is a fragment placed inside layout.html, so the header, the footer and the
data attribution in it exist once.
"""

from __future__ import annotations

import hashlib
import html
from collections.abc import Awaitable, Callable
from importlib import resources

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

STYLESHEET = "/static/site.css"
SCRIPT = "/static/site.js"
ICON = "/static/icon.svg"  # the directory icon, and the favicon for browsers that take SVG
ICON_PNG = "/static/icon-32.png"
TOUCH_ICON = "/static/icon-180.png"
LISTING_ICON = "/static/icon-512.png"  # the PNG uploaded to the directory listings

# path -> (file, link text, title, description)
PAGES = {
    "/": (
        "index.html",
        "Home",
        "Traintrackr: live GB train times in Claude and ChatGPT",
        "A connector that answers questions about National Rail trains: platforms, "
        "live departures and journeys with changes.",
    ),
    "/docs": (
        "docs.html",
        "Docs",
        "Docs · Traintrackr",
        "What Traintrackr can answer, three worked examples, and its limits.",
    ),
    "/privacy": (
        "privacy.html",
        "Privacy",
        "Privacy policy · Traintrackr",
        "What Traintrackr processes about you, why, and for how long.",
    ),
    "/terms": (
        "terms.html",
        "Terms",
        "Terms of use · Traintrackr",
        "The rules for using Traintrackr.",
    ),
}
ASSETS = {
    STYLESHEET: ("site.css", "text/css; charset=utf-8"),
    SCRIPT: ("site.js", "text/javascript; charset=utf-8"),
    ICON: ("icon.svg", "image/svg+xml"),
    ICON_PNG: ("icon-32.png", "image/png"),
    TOUCH_ICON: ("icon-180.png", "image/png"),
    LISTING_ICON: ("icon-512.png", "image/png"),
}
# Browsers ask for /favicon.ico whatever the page links to; answer with the PNG.
FAVICON = "/favicon.ico"

# Nothing is loaded from anywhere else, and nothing inline runs.
HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'self'; script-src 'self'; img-src 'self'; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}


def _read(name: str) -> str:
    return resources.files(__name__).joinpath(name).read_text("utf-8")


def _read_bytes(name: str) -> bytes:
    return resources.files(__name__).joinpath(name).read_bytes()


def asset_url(path: str) -> str:
    """The asset's address with a hash of its content, so a browser that has
    cached the old file fetches the new one as soon as it changes."""
    digest = hashlib.sha256(_read_bytes(ASSETS[path][0])).hexdigest()
    return f"{path}?v={digest[:10]}"


def _nav(current: str) -> str:
    items = []
    for path, (_, text, _, _) in PAGES.items():
        here = ' aria-current="page"' if path == current else ""
        items.append(f'<li><a href="{path}"{here}>{text}</a></li>')
    return "\n".join(items)


def render(path: str, fields: dict[str, str]) -> str:
    """One page as HTML. `fields` fill the {{ name }} places in the page text
    with this server's own values, so a copy deployed elsewhere describes itself."""
    name, _, title, description = PAGES[path]
    page = _read("layout.html")
    # The page goes in first, so that fields are filled in inside it too.
    for key, markup in {"content": _read(name), "nav": _nav(path)}.items():
        page = page.replace("{{ " + key + " }}", markup)
    addresses = {
        name: asset_url(path)
        for name, path in {
            "stylesheet": STYLESHEET,
            "script": SCRIPT,
            "icon": ICON,
            "icon_png": ICON_PNG,
            "touch_icon": TOUCH_ICON,
        }.items()
    }
    for key, text in {"title": title, "description": description, **addresses, **fields}.items():
        page = page.replace("{{ " + key + " }}", html.escape(text))
    return page


def _fixed(
    body: str | bytes, media_type: str, max_age: int
) -> Callable[[Request], Awaitable[Response]]:
    """An endpoint that always answers with `body`; it is rendered once, at start-up."""
    headers = {**HEADERS, "Cache-Control": f"public, max-age={max_age}"}

    async def endpoint(_request: Request) -> Response:
        return Response(body, media_type=media_type, headers=headers)

    return endpoint


def routes(fields: dict[str, str]) -> list[Route]:
    pages = [
        Route(path, _fixed(render(path, fields), "text/html; charset=utf-8", 300)) for path in PAGES
    ]
    assets = [
        Route(path, _fixed(_read_bytes(name), media_type, 3600))
        for path, (name, media_type) in ASSETS.items()
    ]
    favicon = Route(FAVICON, _fixed(_read_bytes(ASSETS[ICON_PNG][0]), "image/png", 86400))
    return [*pages, *assets, favicon]
