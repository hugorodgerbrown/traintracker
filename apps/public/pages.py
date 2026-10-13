"""The site's pages: what Traintrackr is, how to use it, and the privacy policy.

Directory reviewers, and anyone handed the connector URL, need somewhere to
read about it. Each page is a fragment in ``templates/public/pages/``, placed
inside ``public/layout.html`` (views.page), so the header, the footer and the
data attribution exist once. The privacy_policy tool reads the privacy page's
own text (page_text), so the two can't disagree.
"""

import itertools
from html.parser import HTMLParser
from pathlib import Path

from apps.rail.config import Settings
from apps.rail.ratelimit import PLAN_COST

FRAGMENTS = Path(__file__).parent / "templates" / "public" / "pages"

# The link-preview card is 1200 by 630, the shape chat apps show above a
# link's title, and has no transparent corners: a square icon fills the chat.
SHARE_IMAGE_ALT = (
    "The departure board for London Liverpool Street: seven trains, with their "
    "platforms and whether they are on time"
)

# path -> (fragment, link text, title, description)
PAGES = {
    "/": (
        "public/pages/index.html",
        "Home",
        "Traintrackr: live GB train times in Claude and ChatGPT",
        "A connector that answers questions about National Rail trains: platforms, "
        "live departures and journeys with changes.",
    ),
    "/docs": (
        "public/pages/docs.html",
        "Docs",
        "Docs · Traintrackr",
        "What Traintrackr can answer, three worked examples, and its limits.",
    ),
    "/privacy": (
        "public/pages/privacy.html",
        "Privacy",
        "Privacy policy · Traintrackr",
        "What Traintrackr processes about you, why, and for how long.",
    ),
    "/terms": (
        "public/pages/terms.html",
        "Terms",
        "Terms of use · Traintrackr",
        "The rules for using Traintrackr.",
    ),
}

# Header links to pages that are not in PAGES: path -> link text. They come
# after the pages' own links.
EXTRA_NAV = {"/board": "Boards"}


def fields(settings: Settings, site_url: str) -> dict[str, str]:
    """The values the pages name: the MCP address and the fair-use line."""
    if settings.rate_limit_per_minute > 0:
        fair_use = (
            f"Each account can make {max(1, settings.rate_limit_burst)} requests at once and "
            f"{settings.rate_limit_per_minute} a minute; a journey plan counts as {PLAN_COST}. "
            "Over that, the assistant is told how long to wait."
        )
    else:
        fair_use = "There is no set limit on requests; please don't automate them."
    return {"mcp_url": f"{site_url}/mcp", "fair_use": fair_use}


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
    """Return a page's own content, without the layout, as plain Markdown-style text."""
    parser = _TextPage()
    parser.feed((FRAGMENTS / PAGES[path][0].removeprefix("public/pages/")).read_text("utf-8"))
    parser.close()
    text = parser.blocks[0]
    for before, block in itertools.pairwise(parser.blocks):
        # Items of one list sit on consecutive lines; everything else is a paragraph.
        text += ("\n" if before.startswith("- ") and block.startswith("- ") else "\n\n") + block
    return text


def _stop(text: str) -> str:
    """End ``text`` with a full stop unless it already ends a sentence."""
    return text if text.endswith((".", "?", "!")) else text + "."
