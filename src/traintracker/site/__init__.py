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
import itertools
from collections.abc import Awaitable, Callable
from html.parser import HTMLParser
from importlib import resources

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

STYLESHEET = "/static/site.css"
SCRIPT = "/static/site.js"

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
}

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


def asset_url(path: str) -> str:
    """The asset's address with a hash of its content, so a browser that has
    cached the old file fetches the new one as soon as it changes."""
    digest = hashlib.sha256(_read(ASSETS[path][0]).encode()).hexdigest()
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
    addresses = {"stylesheet": asset_url(STYLESHEET), "script": asset_url(SCRIPT)}
    for key, text in {"title": title, "description": description, **addresses, **fields}.items():
        page = page.replace("{{ " + key + " }}", html.escape(text))
    return page


class _TextPage(HTMLParser):
    """A page fragment as Markdown-style text, for reading outside a browser.

    Comments (the TODOs) are dropped. A table row becomes one bullet: its first
    cell in bold, then each other cell after its column heading.
    """

    def __init__(self) -> None:
        super().__init__()
        self.blocks: list[str] = []
        self.text = ""
        self.href = ""
        self.headings: list[str] = []
        self.cells: list[str] = []
        self.in_head = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self.href = dict(attrs).get("href") or ""
        elif tag == "strong":
            self.text += "**"
        elif tag == "thead":
            self.in_head = True

    def handle_endtag(self, tag: str) -> None:
        text = " ".join(self.text.split())
        if tag == "a":
            address = self.href.removeprefix("mailto:")
            if address and address not in self.text:
                self.text += f" ({address})"
        elif tag == "strong":
            self.text += "**"
        elif tag in ("h1", "h2"):
            self._block(f"{'#' * int(tag[1])} {text}")
        elif tag == "p":
            self._block(text)
        elif tag == "li":
            self._block(f"- {text}")
        elif tag in ("th", "td"):
            (self.headings if self.in_head else self.cells).append(text)
            self.text = ""
        elif tag == "thead":
            self.in_head = False
        elif tag == "tr" and self.cells:
            first, *rest = self.cells
            pairs = zip(self.headings[1:], rest, strict=False)
            self._block(f"- **{first}**. " + " ".join(f"{h}: {_stop(c)}" for h, c in pairs))
            self.cells = []

    def handle_data(self, data: str) -> None:
        self.text += data

    def _block(self, text: str) -> None:
        self.blocks.append(text)
        self.text = ""


def page_text(path: str) -> str:
    """A page's own content, without the layout, as plain Markdown-style text."""
    parser = _TextPage()
    parser.feed(_read(PAGES[path][0]))
    parser.close()
    text = parser.blocks[0]
    for before, block in itertools.pairwise(parser.blocks):
        # Items of one list sit on consecutive lines; everything else is a paragraph.
        text += ("\n" if before.startswith("- ") and block.startswith("- ") else "\n\n") + block
    return text


def _stop(text: str) -> str:
    return text if text.endswith((".", "?", "!")) else text + "."


def _fixed(body: str, media_type: str, max_age: int) -> Callable[[Request], Awaitable[Response]]:
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
        Route(path, _fixed(_read(name), media_type, 3600))
        for path, (name, media_type) in ASSETS.items()
    ]
    return pages + assets
