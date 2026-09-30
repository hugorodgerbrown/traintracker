"""Shareable departure boards: a station's live departures at /board/LST, or
one platform's at /board/LST/9, as a page to open, share or embed. Instead of a
platform, a board can be narrowed to the trains calling at another station:
/board/LST/COL. After the station, a station's code is a destination and
anything else a platform; a board is for one or the other, never both.

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
is the one page another site may put in a frame. `?tv=1` gives it alone too,
filling the screen, for a TV (see site.js); the board page's "Show on TV"
button switches to the same view in place and goes full screen.

/board is the picker: choose a station, and a destination or a platform if
you like. The form works without JavaScript; with it, the station fields
suggest names from /api/stations as you type.
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

Fetch = Callable[[str, str | None, str | None], Awaitable[tuple[Board, list[str]]]]

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


def _refused(wait: int) -> dict[str, str]:
    """The headers of an answer over one address's limit. It is that address's
    alone, so no shared cache may keep it and give it to anyone else."""
    return {"Retry-After": str(wait), "Cache-Control": "private, no-store"}


class BoardUnavailable(Exception):
    """The board couldn't be fetched. The message is safe to show."""


# ------------------------------------------------------------------ the data


@dataclass(frozen=True)
class BoardKey:
    """Which board: a station's departures, those calling at `to`, or those
    from one platform. Each has one address."""

    crs: str
    to: str | None = None
    platform: str | None = None

    def __post_init__(self) -> None:
        if self.to and self.platform:
            raise ValueError("A board is for a destination or a platform, not both.")

    @property
    def tail(self) -> str:
        """The address after the station: the destination or the platform."""
        if self.to:
            return f"/{self.to}"
        return f"/{quote(self.platform)}" if self.platform else ""

    @property
    def path(self) -> str:
        return f"{PICKER_PATH}/{self.crs}{self.tail}"

    @property
    def feed(self) -> str:
        return f"{FEED_PREFIX}/{self.crs}{self.tail}"

    def title(self) -> str:
        """ "London Liverpool Street platform 9 departures to Colchester"."""
        text = _name(self.crs) + (f" platform {self.platform}" if self.platform else "")
        return text + " departures" + (f" to {_name(self.to)}" if self.to else "")

    def empty(self) -> str:
        """What the board says when it has no trains."""
        where = f" from platform {self.platform}" if self.platform else ""
        where += f" calling at {_name(self.to)}" if self.to else ""
        return f"No departures{where} in the next two hours."

    def kind(self) -> str:
        """What the board shows, under the station's name: "Platform 9
        departures calling at Colchester"."""
        text = f"Platform {self.platform} departures" if self.platform else "Departures"
        return text + (f" calling at {_name(self.to)}" if self.to else "")


EXAMPLES = (
    BoardKey("LST"),
    BoardKey("LST", "COL"),
    BoardKey("CBG", platform="1"),
    BoardKey("MAN"),
    BoardKey("EDB"),
)


def _name(crs: str | None) -> str:
    station = stations.by_crs(crs or "")
    return station.name if station else crs or ""


def parse(station: str, then: str | None = None) -> BoardKey | None:
    """The board an address names: a station's code, then optionally a
    destination's code or a platform. None if it isn't exactly one; it may
    still name a board loosely (see `_loose`)."""
    st = stations.by_crs(station) if len(station) == 3 else None
    if st is None:
        return None
    if then is None:
        return BoardKey(st.crs)
    if len(then) == 3 and (to := stations.by_crs(then)):
        return BoardKey(st.crs, None if to.crs == st.crs else to.crs)
    platform = _platform(then)
    return BoardKey(st.crs, platform=platform) if platform else None


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


def payload(board: Board, platforms: list[str], key: BoardKey) -> dict[str, Any]:
    """A board as the page draws it, and as /api/board answers."""
    return {
        "station": {"name": board.station.name, "crs": board.station.crs},
        "to": {"name": _name(key.to), "crs": key.to} if key.to else None,
        "platform": key.platform,
        "platforms": platforms,
        "source": board.source,
        "updated": datetime.now(UK_TZ).strftime("%H:%M"),
        "messages": board.messages[:3],
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
        self._pending: dict[BoardKey, asyncio.Future[dict[str, Any] | _Failed]] = {}

    async def get(self, key: BoardKey) -> dict[str, Any]:
        found = self._kept.get(key)
        if found is None:
            if key not in self._pending:
                self._pending[key] = asyncio.ensure_future(self._fetch(key))
                self._pending[key].add_done_callback(lambda _: self._pending.pop(key, None))
            # Shielded: one caller going away doesn't cancel the others' answer.
            found = await asyncio.shield(self._pending[key])
        if isinstance(found, _Failed):
            raise BoardUnavailable(found.message)
        return cast(dict[str, Any], found)

    async def _fetch(self, key: BoardKey) -> dict[str, Any] | _Failed:
        result: dict[str, Any] | _Failed
        try:
            board, platforms = await self.fetch(key.crs, key.to, key.platform)
            result = payload(board, platforms, key)
        except ToolError as exc:
            result = _Failed(str(exc))
        self._kept.set(key, result, self.ttl)
        return result


# ----------------------------------------------------------------- the pages


def _e(text: str) -> str:
    return html.escape(text, quote=True)


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


def _unavailable(key: BoardKey) -> dict[str, Any]:
    """A board with no trains, for when it couldn't be fetched."""
    return {
        "station": {"name": _name(key.crs), "crs": key.crs},
        "to": {"name": _name(key.to), "crs": key.to} if key.to else None,
        "platform": key.platform,
        "platforms": [],
        "source": "darwin",
        "updated": "-",
        "messages": [],
        "trains": [],
    }


def _mode(request: Request) -> str:
    """How the board is shown: "embed" in another site's frame, "tv" on a
    screen of its own, or "page" on the site."""
    if request.query_params.get("embed") == "1":
        return "embed"
    return "tv" if request.query_params.get("tv") == "1" else "page"


def _figure(
    key: BoardKey, data: dict[str, Any], site_url: str, embed: bool, problem: str = ""
) -> str:
    """The board: a table of the trains, which site.js draws as flaps.
    `problem` says why there are no trains in place of the usual message."""
    station = data["station"]
    kind = key.kind()
    now = datetime.now(UK_TZ)
    notes = "".join(f"<li>{_e(m)}</li>" for m in data["messages"])
    # Framed in another page, a link opens a tab of its own.
    back = (
        f' · <a href="{_e(site_url + key.path)}" target="_blank" rel="noopener">Traintrackr</a>'
        if embed
        else ""
    )
    feed = _e(key.feed)
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
    <p class="empty" data-empty="{_e(key.empty())}"{empty}>{_e(problem or key.empty())}</p>
    <p class="stale-note" hidden></p>
    <ul class="notes">{notes}</ul>
    <p class="source"><span class="credit">{_source(data["source"])}</span>{back}
      · <span class="updated">Updated {_e(data["updated"])}</span>
      <span class="status" role="status"></span></p>
    <button type="button" class="fullscreen quiet" hidden>Full screen</button>
  </div>
</figure>"""


def _platform_links(key: BoardKey, platforms: list[str]) -> str:
    """One link for each platform's board, and one for all of them."""
    links = [
        f'<li><a href="{BoardKey(key.crs).path}"'
        + (' aria-current="page"' if key.platform is None else "")
        + ">All</a></li>"
    ]
    for p in platforms:
        if not PLATFORM.fullmatch(p.upper()):
            continue
        here = ' aria-current="page"' if p.upper() == key.platform else ""
        path = BoardKey(key.crs, platform=p.upper()).path
        links.append(f'<li><a href="{path}"{here}>{_e(p)}</a></li>')
    return "\n".join(links)


def _share(key: BoardKey, data: dict[str, Any], site_url: str) -> str:
    """The link to share, the code to embed, and the other platforms' boards."""
    url = site_url + key.path
    frame = (
        f'<iframe src="{url}?embed=1" title="{_e(key.title())}" '
        f'width="100%" height="{EMBED_HEIGHT}" style="border:0;max-width:760px" '
        'loading="lazy"></iframe>'
    )
    others = ""
    if (data["platforms"] or key.platform) and not key.to:
        others = f"""
<h2 id="platforms">One platform</h2>
<p>The board for one platform shows only the trains leaving from it.</p>
<ul class="chips">
{_platform_links(key, data["platforms"])}
</ul>"""
    everywhere = ""
    if key.to:
        everywhere = (
            f' <a href="{BoardKey(key.crs).path}">Every train from {_e(_name(key.crs))}</a>.'
        )
    return f"""<p class="hint">Live times, updated every minute.{everywhere}
  <a href="{PICKER_PATH}">Choose another station</a>.</p>
{others}
<h2 id="share">Share this board</h2>
<p>Anyone with the link sees this board with live times. They don't need to sign in.</p>
<div class="copy">
  <code id="board-link">{_e(url)}</code>
  <button type="button" data-copy="#board-link" hidden>Copy link</button>
  <p role="status"></p>
</div>
<h2 id="tv">Show it on a TV</h2>
<p>The board fills the screen, keeps it awake and hides the cursor. Drag this window onto a
  TV you've extended your display to, or cast this tab, then choose Show on TV. Esc goes back.</p>
<p><button type="button" data-tv hidden>Show on TV</button></p>
<p>On a TV's own browser, or a streaming stick, open the TV link:</p>
<div class="copy">
  <code id="tv-link">{_e(url)}?tv=1</code>
  <button type="button" data-copy="#tv-link" hidden>Copy TV link</button>
  <p role="status"></p>
</div>
<h2 id="embed">Put it on your own page</h2>
<p>Paste this into your page's HTML. The board fills the width it is given, up to 760 pixels.</p>
<div class="copy">
  <code id="embed-code">{_e(frame)}</code>
  <button type="button" data-copy="#embed-code" hidden>Copy code</button>
  <p role="status"></p>
</div>"""


def _picker(
    query: str = "", to: str = "", platform: str = "", problem: str = "", choices: str = ""
) -> str:
    examples = "\n".join(f'<li><a href="{k.path}">{_e(k.title())}</a></li>' for k in EXAMPLES)
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
  <label for="to">Going to <span class="optional">(optional)</span></label>
  <input id="to" name="to" value="{_e(to)}" maxlength="{stations.MAX_QUERY}"
    autocomplete="off" list="to-list" data-suggest="{SUGGEST_PATH}" />
  <datalist id="to-list"></datalist>
  <p class="hint">Only trains that call there, wherever they end up.</p>
  <p class="or">or</p>
  <label for="platform">Platform <span class="optional">(optional)</span></label>
  <input id="platform" name="platform" value="{_e(platform)}" maxlength="4" autocomplete="off"
    inputmode="text" />
  <p class="hint">Only the trains leaving from one platform. Leave both empty for
    every train from the station.</p>
  {error}
  <button type="submit">Show the board</button>
</form>
{choices}
<h2>Examples</h2>
<ul>
{examples}
</ul>"""


def _choices(
    heading: str, candidates: list[stations.Station], field: str, query: dict[str, str]
) -> str:
    """Links back to the picker with `field` set to each candidate's code."""
    items = "\n".join(
        f'<li><a href="{PICKER_PATH}?{_e(urlencode(query | {field: s.crs}))}">{_e(s.name)}</a>'
        f" ({s.crs})</li>"
        for s in candidates
    )
    return f'<h2 id="which">{_e(heading)}</h2>\n<ul class="choices">\n{items}\n</ul>'


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


def _loose(station: str, then: str | None) -> BoardKey | None:
    """The board an address names with a station's name in place of a code
    (/board/cambridge, /board/LST/colchester), or a platform written "p9"."""
    found = _match(station)
    if not isinstance(found, stations.Station):
        return None
    if then is None:
        return BoardKey(found.crs)
    if key := parse(found.crs, then):
        return key
    to = _match(then)
    if not isinstance(to, stations.Station):
        return None
    return BoardKey(found.crs, None if to.crs == found.crs else to.crs)


def _field(then: str) -> str:
    """Which picker field an unreadable second part of an address goes back to."""
    return "platform" if len(then) <= 4 or any(c.isdigit() for c in then) else "to"


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
            headers={**PAGE_HEADERS, **_refused(wait)},
            media_type="text/plain",
        )

    def page(
        request: Request, path: str, content: str, title: str, description: str, status: int = 200
    ) -> Response:
        mode = _mode(request)
        text = render_page(
            path,
            content,
            title,
            description,
            fields,
            nav=PICKER_PATH,
            layout={"embed": "embed.html", "tv": "tv.html"}.get(mode, "layout.html"),
        )
        headers = {
            **(EMBED_HEADERS if mode == "embed" else PAGE_HEADERS),
            "Cache-Control": "public, max-age=30",
        }
        return Response(text, status, headers=headers, media_type="text/html; charset=utf-8")

    def picker(
        request: Request,
        query: dict[str, str] | None = None,
        problem: str = "",
        choices: str = "",
        status: int = 200,
    ) -> Response:
        typed = query or {}
        return page(
            request,
            PICKER_PATH,
            _picker(
                typed.get("station", ""),
                typed.get("to", ""),
                typed.get("platform", ""),
                problem,
                choices,
            ),
            "Departure boards · Traintrackr",
            "A live departure board for any station in Great Britain, to share or embed.",
            status,
        )

    async def pick(request: Request) -> Response:
        typed = {
            "station": request.query_params.get("station", "").strip()[: stations.MAX_QUERY],
            "to": request.query_params.get("to", "").strip()[: stations.MAX_QUERY],
            "platform": request.query_params.get("platform", "").strip()[:12],
        }
        typed = {k: v for k, v in typed.items() if v}
        if not typed.get("station"):
            return picker(request, typed)
        platform = _platform(typed["platform"]) if "platform" in typed else None
        if "platform" in typed and not platform:
            problem = (
                f"'{typed['platform']}' isn't a platform. Use its number or letter, like 9 or 10A."
            )
            return picker(request, typed, problem, status=400)
        if platform and "to" in typed:
            problem = "A board is for a destination or a platform, not both. Clear one of them."
            return picker(request, typed, problem, status=400)
        found: dict[str, stations.Station] = {}
        for field, heading in (("station", "Which station?"), ("to", "Going to which station?")):
            if field not in typed:
                continue
            match = _match(typed[field])
            if isinstance(match, stations.Station):
                found[field] = match
                continue
            if not match:
                problem = (
                    f"No station matches '{typed[field]}'. Check the spelling, "
                    "or use its three-letter code."
                )
                return picker(request, typed, problem, status=404)
            return picker(request, typed, choices=_choices(heading, match, field, typed))
        crs = found["station"].crs
        to = found["to"].crs if "to" in found and found["to"].crs != crs else None
        return RedirectResponse(BoardKey(crs, to, platform).path, 303)

    async def show(request: Request) -> Response:
        given = request.path_params["station"][: stations.MAX_QUERY]
        then = request.path_params.get("then")
        then = then[: stations.MAX_QUERY] if then is not None else None
        key = parse(given, then) or _loose(given, then)
        if key is None:
            # Not a board: the picker says why, or asks which station.
            query = {"station": given}
            if then:
                query[_field(then)] = then
            return RedirectResponse(f"{PICKER_PATH}?{urlencode(query)}", 303)
        # One address for each board, so every share and embed of it is the same.
        if request.url.path != key.path:
            target = key.path
            if request.url.query:
                target += "?" + request.url.query
            return RedirectResponse(target, 308)
        if limit := refused(request):
            return limit
        try:
            data = await boards.get(key)
        except BoardUnavailable as exc:
            mode = _mode(request)
            if mode == "page":
                typed_back = {
                    "station": key.crs,
                    "to": key.to or "",
                    "platform": key.platform or "",
                }
                return picker(request, typed_back, str(exc), status=503)
            # A TV or an embed has nobody to read a form: it shows the board
            # with the reason in it, and recovers by itself when the next
            # fetch works.
            content = _figure(key, _unavailable(key), site_url, mode == "embed", str(exc))
            return page(request, key.path, content, f"{key.title()} · Traintrackr", "", status=503)
        mode = _mode(request)
        content = _figure(key, data, site_url, mode == "embed")
        if mode == "page":
            content += "\n" + _share(key, data, site_url)
        return page(
            request,
            key.path,
            content,
            f"{key.title()} · Traintrackr",
            f"Live {key.title()}, updated every minute.",
        )

    async def feed(request: Request) -> Response:
        key = parse(request.path_params["station"], request.path_params.get("then"))
        if key is None:
            return JSONResponse({"error": "No such board."}, 404, headers=FEED_HEADERS)
        if wait := limiter.take(client_address(request.scope, header)):
            return JSONResponse(
                {"error": limited(wait)}, 429, headers={**FEED_HEADERS, **_refused(wait)}
            )
        try:
            board = await boards.get(key)
            return JSONResponse(board, headers=FEED_HEADERS)
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
        Route(PICKER_PATH + "/{station}/{then}", show, methods=["GET", "HEAD"]),
        Route(FEED_PREFIX + "/{station}", feed, methods=["GET"]),
        Route(FEED_PREFIX + "/{station}/{then}", feed, methods=["GET"]),
        Route(SUGGEST_PATH, suggest, methods=["GET"]),
    ]
