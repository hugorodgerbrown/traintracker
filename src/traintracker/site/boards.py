"""Shareable departure boards: a station's live departures at /board/LST, or
one platform's at /board/LST/9, as a page to open, share or embed.

The page is the homepage's split-flap board with live trains. It is rendered
with the trains in it, as a table, so it reads without JavaScript; site.js
draws the flaps, ticks the clock and fetches the board again from
/api/board/LST/9 every minute while the page is visible.

Anyone can open a board without signing in, and a board embedded in a busy
page is opened by everyone who visits it. So what reaches Darwin is one fetch
per board a minute, not one per viewer: each answer is kept for TTL seconds
and shared by every page showing that board, and while it is being fetched
other requests for it wait for the same answer. Each client address may open
boards at a limited rate as well, which bounds what one address can make the
server fetch by asking for many different boards.

`?embed=1` gives the board alone, without the site's header and footer, and it
is the one page another site may put in a frame.

/board is the picker: choose a station, and a platform if you like. The form
works without JavaScript; with it, the station field suggests names from
/api/stations as you type.
"""

from __future__ import annotations

import asyncio
import html
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast
from urllib.parse import quote, urlencode

from mcp.server.mcpserver.exceptions import ToolError
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from traintracker import stations
from traintracker.config import UK_TZ
from traintracker.errors import StationNotFound
from traintracker.http import TTLCache
from traintracker.models import Board, BoardService
from traintracker.ratelimit import RateLimiter, client_address, limited
from traintracker.site import HEADERS, HSTS, render_page

PICKER_PATH = "/board"
FEED_PREFIX = "/api/board"
SUGGEST_PATH = "/api/stations"

TTL = 60.0  # seconds one fetched board is shared for
REFRESH = 60  # seconds between a page's fetches while it is visible
MAX_BOARDS = 2048  # boards kept at once; about one per station
MAX_SUGGESTIONS = 8
# What one client address may do: (at once, a minute). A page open on a screen
# takes one a minute; this leaves room for an office of screens behind one address.
BOARD_LIMIT = (30, 60.0)
# A platform as the board's address has it: "9", "10A", "B".
PLATFORM = re.compile(r"[0-9A-Z]{1,4}")
# "London Liverpool Street (LST)", as a suggestion fills the station field.
WITH_CODE = re.compile(r".*\(([A-Za-z]{3})\)\s*")
# The size the embed code gives the frame: ten trains in the wide layout.
EMBED_HEIGHT = 560
# The score (0-100) a station needs to be offered as "did you mean". Every
# query matches something a little.
CLOSE_ENOUGH = 70
EXAMPLES = (("LST", None), ("CBG", "1"), ("MAN", None), ("EDB", None))

Fetch = Callable[[str, str | None], Awaitable[tuple[Board, list[str]]]]

_POLICY = (
    "default-src 'none'; style-src 'self'; script-src 'self'; img-src 'self'; "
    "connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors {frame}"
)
PAGE_HEADERS = {**HEADERS, "Content-Security-Policy": _POLICY.format(frame="'none'")}
# An embedded board is meant to be framed by any site.
EMBED_HEADERS = {**HEADERS, "Content-Security-Policy": _POLICY.format(frame="*")}
FEED_HEADERS = {
    "Strict-Transport-Security": HSTS,
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "public, max-age=30",
}


class BoardUnavailable(Exception):
    """The board couldn't be fetched. The message is safe to show."""


# ------------------------------------------------------------------ the data


def board_path(crs: str, platform: str | None = None) -> str:
    return f"{PICKER_PATH}/{crs}" + (f"/{quote(platform)}" if platform else "")


def feed_path(crs: str, platform: str | None = None) -> str:
    return f"{FEED_PREFIX}/{crs}" + (f"/{quote(platform)}" if platform else "")


def _train(service: BoardService) -> dict[str, str]:
    """What each column of the board says for one train; the same as the
    chat's board app (ui/board.html) shows."""
    expected = "Cancelled" if service.status == "cancelled" else service.expected or ""
    return {
        "time": service.scheduled or "",
        "place": " & ".join(p.name for p in service.destination),
        "platform": service.platform or "",
        "expected": expected.removesuffix("*"),
    }


def payload(board: Board, platforms: list[str], platform: str | None) -> dict[str, Any]:
    """A board as the page draws it, and as /api/board answers."""
    messages = list(board.messages[:3])
    if platform and not board.services:
        messages.append(f"No departures from platform {platform} in the next two hours.")
    return {
        "station": {"name": board.station.name, "crs": board.station.crs},
        "platform": platform,
        "platforms": platforms,
        "source": board.source,
        "updated": datetime.now(UK_TZ).strftime("%H:%M"),
        "messages": messages,
        "trains": [_train(s) for s in board.services],
    }


@dataclass
class _Failed:
    message: str


class SharedBoards:
    """Fetched boards, each shared by every request for it for `ttl` seconds.

    A failure is kept as long, so a board that can't be fetched isn't asked
    for again by every page showing it.
    """

    def __init__(self, fetch: Fetch, ttl: float = TTL) -> None:
        self.fetch = fetch
        self.ttl = ttl
        self._kept = TTLCache(MAX_BOARDS)
        self._pending: dict[tuple[str, str | None], asyncio.Future[dict[str, Any] | _Failed]] = {}

    async def get(self, crs: str, platform: str | None) -> dict[str, Any]:
        key = (crs, platform)
        found = self._kept.get(key)
        if found is None:
            if key not in self._pending:
                self._pending[key] = asyncio.ensure_future(self._fetch(crs, platform))
                self._pending[key].add_done_callback(lambda _: self._pending.pop(key, None))
            # Shielded: one caller going away doesn't cancel the others' answer.
            found = await asyncio.shield(self._pending[key])
        if isinstance(found, _Failed):
            raise BoardUnavailable(found.message)
        return cast(dict[str, Any], found)

    async def _fetch(self, crs: str, platform: str | None) -> dict[str, Any] | _Failed:
        result: dict[str, Any] | _Failed
        try:
            board, platforms = await self.fetch(crs, platform)
            result = payload(board, platforms, platform)
        except ToolError as exc:
            result = _Failed(str(exc))
        self._kept.set((crs, platform), result, self.ttl)
        return result


# ----------------------------------------------------------------- the pages


def _e(text: str) -> str:
    return html.escape(text, quote=True)


def _title(station: str, platform: str | None) -> str:
    return f"{station} platform {platform} departures" if platform else f"{station} departures"


def _rows(trains: list[dict[str, str]]) -> str:
    rows = []
    for t in trains:
        rows.append(
            "<tr>"
            f"<td>{_e(t['time'])}</td>"
            f'<td data-label="Destination">{_e(t["place"])}</td>'
            f'<td data-label="Platform">{_e(t["platform"])}</td>'
            f'<td data-label="Expected">{_e(t["expected"])}</td>'
            "</tr>"
        )
    return "\n".join(rows)


def _source(source: str) -> str:
    if source == "darwin":
        return (
            '<a href="https://www.nationalrail.co.uk/" target="_blank" rel="noopener">'
            "Powered by National Rail Enquiries</a>"
        )
    return "Booked times: Network Rail data feeds (OGL v3.0)"


def _figure(data: dict[str, Any], site_url: str, embed: bool) -> str:
    """The board: a table of the trains, which site.js draws as flaps."""
    station = data["station"]
    platform = data["platform"]
    kind = f"Platform {platform} departures" if platform else "Departures"
    now = datetime.now(UK_TZ)
    notes = "".join(f"<li>{_e(m)}</li>" for m in data["messages"])
    # Framed in another page, a link opens a tab of its own.
    back = (
        f' · <a href="{_e(site_url + board_path(station["crs"], platform))}" target="_blank" '
        'rel="noopener">Traintrackr</a>'
        if embed
        else ""
    )
    feed = _e(feed_path(station["crs"], platform))
    empty = "" if not data["trains"] else " hidden"
    return f"""<figure class="departures live" data-feed="{feed}" data-refresh="{REFRESH}">
  <div class="panel">
    <div class="station">
      <h1>{_e(station["name"])}</h1>
      <span>{_e(kind)}</span>
      <time class="clock" datetime="{now.isoformat(timespec="seconds")}">{now:%H:%M:%S}</time>
    </div>
    <div class="trains">
      <table>
        <caption>{_e(kind)} from {_e(station["name"])}</caption>
        <thead><tr><th>Time</th><th>Destination</th><th>Plat</th><th>Expected</th></tr></thead>
        <tbody>
{_rows(data["trains"])}
        </tbody>
      </table>
    </div>
    <p class="empty"{empty}>No departures in the next two hours.</p>
    <ul class="notes">{notes}</ul>
    <p class="source"><span class="credit">{_source(data["source"])}</span>{back}
      · <span class="updated">Updated {_e(data["updated"])}</span>
      <span class="status" role="status"></span></p>
  </div>
</figure>"""


def _platform_links(crs: str, platforms: list[str], current: str | None) -> str:
    links = [
        f'<li><a href="{board_path(crs)}"'
        + (' aria-current="page"' if current is None else "")
        + ">All</a></li>"
    ]
    for p in platforms:
        if not PLATFORM.fullmatch(p.upper()):
            continue
        here = ' aria-current="page"' if p.upper() == current else ""
        links.append(f'<li><a href="{board_path(crs, p.upper())}"{here}>{_e(p)}</a></li>')
    return "\n".join(links)


def _share(data: dict[str, Any], site_url: str) -> str:
    """The link to share, the code to embed, and the other platforms' boards."""
    station = data["station"]
    platform = data["platform"]
    url = site_url + board_path(station["crs"], platform)
    frame = (
        f'<iframe src="{url}?embed=1" title="{_e(_title(station["name"], platform))}" '
        f'width="100%" height="{EMBED_HEIGHT}" style="border:0;max-width:760px" '
        'loading="lazy"></iframe>'
    )
    others = ""
    if data["platforms"] or platform:
        others = f"""
<h2 id="platforms">One platform</h2>
<p>The board for one platform shows only the trains leaving from it.</p>
<ul class="chips">
{_platform_links(station["crs"], data["platforms"], platform)}
</ul>"""
    return f"""<p class="hint">Live times, updated every minute.
  <a href="{PICKER_PATH}">Choose another station</a>.</p>
{others}
<h2 id="share">Share this board</h2>
<p>Anyone with the link sees this board with live times. They don't need to sign in.</p>
<div class="copy">
  <code id="board-link">{_e(url)}</code>
  <button type="button" data-copy="#board-link" hidden>Copy link</button>
  <p role="status"></p>
</div>
<h2 id="embed">Put it on your own page</h2>
<p>Paste this into your page's HTML. The board fills the width it is given, up to 760 pixels.</p>
<div class="copy">
  <code id="embed-code">{_e(frame)}</code>
  <button type="button" data-copy="#embed-code" hidden>Copy code</button>
  <p role="status"></p>
</div>"""


def _picker(query: str = "", platform: str = "", problem: str = "", choices: str = "") -> str:
    examples = "\n".join(
        f'<li><a href="{board_path(crs, p)}">{_e(_title(stations.by_crs(crs).name, p))}</a></li>'  # type: ignore[union-attr]
        for crs, p in EXAMPLES
        if stations.by_crs(crs)
    )
    error = f'<p class="error" id="picker-error">{_e(problem)}</p>' if problem else ""
    described = ' aria-describedby="picker-error"' if problem else ""
    return f"""<h1>Departure boards</h1>
<p class="lede">A live board for any station in Great Britain, to keep open,
  share or put on your own page.</p>
<form class="picker" method="get" action="{PICKER_PATH}">
  <label for="station">Station</label>
  <input id="station" name="station" value="{_e(query)}" required maxlength="{stations.MAX_QUERY}"
    autocomplete="off" list="station-list" data-suggest="{SUGGEST_PATH}"{described} />
  <datalist id="station-list"></datalist>
  <label for="platform">Platform <span class="optional">(optional)</span></label>
  <input id="platform" name="platform" value="{_e(platform)}" maxlength="4" autocomplete="off"
    inputmode="text" />
  <p class="hint">Leave the platform empty for every train from the station.</p>
  {error}
  <button type="submit">Show the board</button>
</form>
{choices}
<h2>Examples</h2>
<ul>
{examples}
</ul>"""


def _choices(candidates: list[stations.Station], platform: str | None) -> str:
    items = "\n".join(
        f'<li><a href="{board_path(s.crs, platform)}">{_e(s.name)}</a> ({s.crs})</li>'
        for s in candidates
    )
    return f'<h2 id="which">Which station?</h2>\n<ul class="choices">\n{items}\n</ul>'


# ---------------------------------------------------------------- the routes


def _platform(text: str) -> str | None:
    """A platform as the board's address has it, or None if it can't be one."""
    text = re.sub(r"^(platform|plat|pl|p)[\s-]*", "", text.strip(), flags=re.I).upper()
    return text if PLATFORM.fullmatch(text) else None


def _match(query: str) -> stations.Station | list[stations.Station]:
    """The one station `query` names, or the candidates if it names several
    (none if it names nothing)."""
    if m := WITH_CODE.fullmatch(query):
        query = m.group(1)
    query = query.replace("-", " ").replace("_", " ").strip()
    try:
        return stations.resolve(query)
    except StationNotFound:
        return [s for s, score in stations.search(query, 5) if score >= CLOSE_ENOUGH]


def routes(fields: dict[str, str], fetch: Fetch, ip_header: str | None = None) -> list[Route]:
    """The picker, the boards, and the data the pages fetch."""
    boards = SharedBoards(fetch)
    limiter = RateLimiter(BOARD_LIMIT[1], BOARD_LIMIT[0])
    header = ip_header.lower().encode() if ip_header else None
    site_url = fields["site_url"]

    def refused(request: Request) -> Response | None:
        wait = limiter.take(client_address(request.scope, header))
        if not wait:
            return None
        return Response(
            limited(wait),
            429,
            headers={**PAGE_HEADERS, "Retry-After": str(wait)},
            media_type="text/plain",
        )

    def page(
        request: Request, path: str, content: str, title: str, description: str, status: int = 200
    ) -> Response:
        embed = request.query_params.get("embed") == "1"
        text = render_page(
            path,
            content,
            title,
            description,
            fields,
            nav=PICKER_PATH,
            layout="embed.html" if embed else "layout.html",
        )
        headers = {
            **(EMBED_HEADERS if embed else PAGE_HEADERS),
            "Cache-Control": "public, max-age=30",
        }
        return Response(text, status, headers=headers, media_type="text/html; charset=utf-8")

    def picker(
        request: Request,
        query: str = "",
        platform: str = "",
        problem: str = "",
        choices: str = "",
        status: int = 200,
    ) -> Response:
        return page(
            request,
            PICKER_PATH,
            _picker(query, platform, problem, choices),
            "Departure boards · Traintrackr",
            "A live departure board for any station in Great Britain, to share or embed.",
            status,
        )

    async def pick(request: Request) -> Response:
        query = request.query_params.get("station", "").strip()[: stations.MAX_QUERY]
        typed = request.query_params.get("platform", "").strip()[:12]
        if not query:
            return picker(request)
        platform = _platform(typed) if typed else None
        if typed and not platform:
            problem = f"'{typed}' isn't a platform. Use its number or letter, like 9 or 10A."
            return picker(request, query, typed, problem, status=400)
        found = _match(query)
        if isinstance(found, stations.Station):
            return RedirectResponse(board_path(found.crs, platform), 303)
        if not found:
            problem = (
                f"No station matches '{query}'. Check the spelling, or use its three-letter code."
            )
            return picker(request, query, typed, problem, status=404)
        return picker(request, query, typed, choices=_choices(found, platform))

    async def show(request: Request) -> Response:
        given = request.path_params["station"][: stations.MAX_QUERY]
        typed = request.path_params.get("platform")
        platform = _platform(typed) if typed is not None else None
        found = stations.by_crs(given) if len(given) == 3 else None
        if found is None and (match := _match(given)) and isinstance(match, stations.Station):
            found = match
        if found is None or (typed is not None and platform is None):
            # Not a board: the picker says why, or asks which station.
            query = {"station": given} | ({"platform": typed[:12]} if typed else {})
            return RedirectResponse(f"{PICKER_PATH}?{urlencode(query)}", 303)
        # One address for each board, so every share and embed of it is the same.
        if given != found.crs or typed != platform:
            target = board_path(found.crs, platform)
            if request.url.query:
                target += "?" + request.url.query
            return RedirectResponse(target, 308)
        if limit := refused(request):
            return limit
        try:
            data = await boards.get(found.crs, platform)
        except BoardUnavailable as exc:
            return picker(request, found.name, platform or "", str(exc), status=503)
        embed = request.query_params.get("embed") == "1"
        content = _figure(data, site_url, embed)
        if not embed:
            content += "\n" + _share(data, site_url)
        name = data["station"]["name"]
        where = f"platform {platform} at {name}" if platform else name
        return page(
            request,
            board_path(found.crs, platform),
            content,
            f"{_title(name, platform)} · Traintrackr",
            f"Live departures from {where}, updated every minute.",
        )

    async def feed(request: Request) -> Response:
        crs = request.path_params["station"].upper()
        typed = request.path_params.get("platform")
        platform = _platform(typed) if typed is not None else None
        if stations.by_crs(crs) is None or (typed is not None and platform is None):
            return JSONResponse({"error": "No such board."}, 404, headers=FEED_HEADERS)
        if wait := limiter.take(client_address(request.scope, header)):
            return JSONResponse(
                {"error": limited(wait)}, 429, headers={**FEED_HEADERS, "Retry-After": str(wait)}
            )
        try:
            return JSONResponse(await boards.get(crs, platform), headers=FEED_HEADERS)
        except BoardUnavailable as exc:
            return JSONResponse({"error": str(exc)}, 503, headers=FEED_HEADERS)

    async def suggest(request: Request) -> Response:
        query = request.query_params.get("q", "")
        found = (
            [{"name": s.name, "crs": s.crs} for s, _ in stations.search(query, MAX_SUGGESTIONS)]
            if len(query.strip()) >= 2
            else []
        )
        headers = {**FEED_HEADERS, "Cache-Control": "public, max-age=86400"}
        return JSONResponse(found, headers=headers)

    return [
        Route(PICKER_PATH, pick, methods=["GET", "HEAD"]),
        Route(PICKER_PATH + "/{station}", show, methods=["GET", "HEAD"]),
        Route(PICKER_PATH + "/{station}/{platform}", show, methods=["GET", "HEAD"]),
        Route(FEED_PREFIX + "/{station}", feed, methods=["GET"]),
        Route(FEED_PREFIX + "/{station}/{platform}", feed, methods=["GET"]),
        Route(SUGGEST_PATH, suggest, methods=["GET"]),
    ]
