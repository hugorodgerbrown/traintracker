"""Django's site says and links exactly what production's does, page for page.

Until Titan 4/4 both exist: production serves ``src/traintracker/site`` from
``traintracker serve-http``, and Django will serve ``apps/public`` from the
cut-over. Each address below is asked of both, with the same boards behind
them; the status, the redirect and what a reader gets (the words, the links,
the form fields and the data site.js reads) must be the same. Only where the
static files live and the clock's second may differ.
"""

import asyncio
import re
from html.parser import HTMLParser
from typing import Any

import httpx
import pytest
from django.test import Client

# Static files are /static/site.css?v=... in production and
# /static/css/site.css (hashed when DEBUG is off) in Django.
_STATIC = re.compile(r"/static/(?:[\w-]+/)*([\w-]+?)(?:\.[0-9a-f]{12})?\.(\w+)(?:\?v=\w+)?")
# Attributes that hold the time of the request, not the board.
_NOW = {"datetime"}


class _Reading(HTMLParser):
    """A page as a reader and site.js meet it: tags with their attributes, and the text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tokens: list[Any] = []
        self.in_clock = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        kept = sorted(
            (name, _STATIC.sub(r"\1.\2", value or "")) for name, value in attrs if name not in _NOW
        )
        self.tokens.append((tag, kept))
        self.in_clock = tag == "time"

    def handle_endtag(self, tag: str) -> None:
        self.in_clock = False
        self.tokens.append(f"/{tag}")

    def handle_data(self, data: str) -> None:
        # Each app fetches its own board, a second or so apart.
        text = re.sub(r"Updated \d\d:\d\d:\d\d", "Updated", " ".join(data.split()))
        if text and not self.in_clock:
            self.tokens.append(text)


def reading(html: str) -> list[Any]:
    """The tokens of ``html``: the head's in any order, the body's in order."""
    parser = _Reading()
    parser.feed(html)
    parser.close()
    merged: list[Any] = []
    for token in parser.tokens:
        if isinstance(token, str) and not token.startswith("/") and merged:
            last = merged[-1]
            if isinstance(last, str) and not last.startswith("/"):
                merged[-1] = f"{last} {token}"
                continue
        merged.append(token)
    starts = [i for i, t in enumerate(merged) if isinstance(t, tuple) and t[0] == "body"]
    if not starts:
        return merged
    head, body = merged[: starts[0]], merged[starts[0] :]
    return [sorted(map(repr, head)), *body]


def _data(body: Any) -> Any:
    """A board's data without the second it was fetched."""
    return {k: v for k, v in body.items() if k != "updated"} if isinstance(body, dict) else body


ADDRESSES = [
    "/",
    "/docs",
    "/privacy",
    "/terms",
    "/board",
    "/board?station=sudbury&platform=2",
    "/board?station=zzqqxxjj",
    "/board?station=LST&platform=%3Cb%3E",
    "/board?station=LST&to=Colchester&platform=9",
    "/board?station=LST&to=Colchester",
    "/board?station=Cambridge&platform=1",
    "/board/LST",
    "/board/LST/9",
    "/board/LST/COL",
    "/board/LST?embed=1",
    "/board/LST/9?tv=1",
    "/board/lst",
    "/board/cambridge/1?embed=1",
    "/board/sudbury",
    "/board/LST/12!",
    "/api/board/LST",
    "/api/board/LST/COL",
    "/api/board/XYZ",
    "/api/stations?q=liver",
    "/api/stations?q=l",
]


@pytest.mark.django_db
@pytest.mark.parametrize("address", ADDRESSES)
async def test_django_serves_what_production_serves(
    production: httpx.AsyncClient, client: Client, address: str
) -> None:
    """The same answer, in what anyone (or site.js) can read of it."""
    old = await production.get(address)
    new = await asyncio.to_thread(client.get, address)
    assert new.status_code == old.status_code
    assert new.get("Location") == old.headers.get("location")
    if old.headers.get("content-type", "").startswith("application/json"):
        assert _data(new.json()) == _data(old.json())
    elif old.status_code < 300:
        assert reading(new.content.decode()) == reading(old.text)


@pytest.mark.django_db
async def test_a_board_that_cannot_be_had_reads_the_same(
    production: httpx.AsyncClient, client: Client, fetch: Any
) -> None:
    """Darwin down: the page offers the picker, a TV or an embed says why, in both."""
    fetch.error = "Live times are unavailable right now."
    for address in ("/board/LST", "/board/LST?tv=1", "/board/LST?embed=1", "/api/board/LST"):
        old = await production.get(address)
        new = await asyncio.to_thread(client.get, address)
        assert new.status_code == old.status_code == 503, address
        if address.startswith("/api/"):
            assert new.json() == old.json()
        else:
            assert reading(new.content.decode()) == reading(old.text), address
