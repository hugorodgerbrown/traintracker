"""Shareable departure boards: /board, /board/LST, /board/LST/9, /board/LST/COL.

What a board is, how its address reads and how every viewer of one minute
shares one Darwin fetch is board.py; these views draw the pages. The shared
boards live on the train tools' event loop (apps/rail/runtime.py), where the
board fetch runs.

``?embed=1`` is the one page another site may frame; ``?tv=1`` fills a
screen. Each client address may open boards at a limited rate, which bounds
what one address can make the server fetch: a token bucket, a burst of 30
refilled at 60 a minute, kept in the process's memory (one worker serves the
site: docs/decisions/the-train-tools-run-on-one-event-loop.md).
"""

import logging
from datetime import datetime
from functools import cache
from typing import Any
from urllib.parse import urlencode

from django.conf import settings
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_safe

from apps.core.ratelimit import client_ip
from apps.public import board as tt
from apps.public import pages
from apps.public.views import page_context, relative
from apps.rail import runtime, server, stations
from apps.rail.config import UK_TZ
from apps.rail.ratelimit import RateLimiter, limited

logger = logging.getLogger(__name__)

# The board on the landing page: the station the share card draws, and as
# many trains as the card shows.
HOME_BOARD = tt.BoardKey("LST")
HOME_ROWS = 7

BURST, PER_MINUTE = int(tt.BOARD_LIMIT[0]), tt.BOARD_LIMIT[1]
PICKER_TITLE = "Departure boards · Traintrackr"
PICKER_DESCRIPTION = "A live departure board for any station in Great Britain, to share or embed."


@cache
def shared() -> tt.SharedBoards:
    """The process's boards, each fetched once a minute for every viewer."""
    return tt.SharedBoards(server.public_board)


@cache
def limiter() -> RateLimiter:
    """The process's buckets, one for each client address."""
    return RateLimiter(PER_MINUTE, BURST)


def _wait(request: HttpRequest) -> int:
    """Seconds this address must wait, or 0 if it may open a board now."""
    return limiter().take(client_ip(request))


def _refused(wait: int, response: HttpResponse) -> HttpResponse:
    """An answer over one address's limit: that address's alone, kept by no cache."""
    response["Retry-After"] = str(wait)
    response["Cache-Control"] = "private, no-store"
    return response


def _mode(request: HttpRequest) -> str:
    """How the board is shown: in another site's frame, on a TV, or as a page.

    Returns "embed", "tv" or "page".
    """
    if request.GET.get("embed") == "1":
        return "embed"
    return "tv" if request.GET.get("tv") == "1" else "page"


def _figure_context(
    key: tt.BoardKey, data: dict[str, Any], mode: str, problem: str = ""
) -> dict[str, Any]:
    """A board's own page around public/_figure.html: its title, sharing and header."""
    title = f"{key.title()} · Traintrackr"
    description = "" if problem else f"Live {key.title()}, updated every minute."
    return page_context(key.path, title, description, "/board") | _board_fields(
        key, data, mode, problem
    )


def _board_fields(
    key: tt.BoardKey, data: dict[str, Any], mode: str, problem: str = ""
) -> dict[str, Any]:
    """What public/_figure.html draws: the board, its trains and its notices."""
    return {
        "key": key,
        "board_path": relative(key.path),
        "mode": mode,
        "board": data,
        "kind": key.kind(),
        "empty": key.empty(),
        "problem": problem,
        "refresh": tt.REFRESH,
        "now": datetime.now(UK_TZ),
        "trains": [t | {"said": tt.said(t["time"], t["expected"])} for t in data["trains"]],
    }


def _board_page(
    request: HttpRequest, key: tt.BoardKey, data: dict[str, Any], status: int = 200
) -> HttpResponse:
    """The board on the site, with how to share, show and embed it."""
    context = _figure_context(key, data, "page")
    url = context["page_url"]
    platform_links = []
    if (data["platforms"] or key.platform) and not key.to:
        platform_links = [(relative(tt.BoardKey(key.crs).path), "All", key.platform is None)] + [
            (relative(tt.BoardKey(key.crs, platform=p.upper()).path), p, p.upper() == key.platform)
            for p in data["platforms"]
            if tt.PLATFORM.fullmatch(p.upper())
        ]
    context |= {
        "platform_links": platform_links,
        "all_path": relative(tt.BoardKey(key.crs).path),
        "station_name": tt.station_name(key.crs),
        "embed_code": (
            f'<iframe src="{url}?embed=1" title="{key.title()}" width="100%" '
            f'height="{tt.EMBED_HEIGHT}" style="border:0;max-width:760px" loading="lazy"></iframe>'
        ),
    }
    response = render(request, "public/board.html", context, status=status)
    response["Cache-Control"] = "public, max-age=30"
    return response


def _frame(
    request: HttpRequest, key: tt.BoardKey, data: dict[str, Any], mode: str, problem: str = ""
) -> HttpResponse:
    """The board alone, for an embed or a TV; with no trains and why, if it couldn't be had."""
    context = _figure_context(key, data, mode, problem)
    response = render(request, "public/frame.html", context, status=503 if problem else 200)
    response["Cache-Control"] = "public, max-age=30"
    if mode == "embed":
        # An embedded board is meant to be framed by any site.
        response.xframe_options_exempt = True
        response._csp_config = {**settings.SECURE_CSP, "frame-ancestors": ["*"]}  # type: ignore[attr-defined]
    return response


def _see_other(url: str) -> HttpResponseRedirect:
    """A 303: the answer to a GET form is another page, fetched with GET."""
    response = HttpResponseRedirect(url)
    response.status_code = 303
    return response


def _picker(
    request: HttpRequest,
    typed: dict[str, str] | None = None,
    problem: str = "",
    choices: dict[str, Any] | None = None,
    status: int = 200,
) -> HttpResponse:
    """The picker, with what was typed, why it didn't make a board, or which station."""
    context = page_context("/board", PICKER_TITLE, PICKER_DESCRIPTION, "/board") | {
        "typed": typed or {},
        "problem": problem,
        "choices": choices,
        "max_query": stations.MAX_QUERY,
        "examples": [(relative(k.path), k.title()) for k in tt.EXAMPLES],
    }
    response = render(request, "public/picker.html", context, status=status)
    response["Cache-Control"] = "public, max-age=30"
    return response


def _choices(
    heading: str, candidates: list[stations.Station], field: str, typed: dict[str, str]
) -> dict[str, Any]:
    """Links back to the picker with ``field`` set to each candidate's code."""
    return {
        "heading": heading,
        "options": [
            (s.name, s.crs, f"{relative(tt.PICKER_PATH)}?{urlencode(typed | {field: s.crs})}")
            for s in candidates
        ],
    }


@require_safe
def pick(request: HttpRequest) -> HttpResponse:
    """The picker; with a station chosen, the board it names."""
    typed = {
        "station": request.GET.get("station", "").strip()[: stations.MAX_QUERY],
        "to": request.GET.get("to", "").strip()[: stations.MAX_QUERY],
        "platform": request.GET.get("platform", "").strip()[:12],
    }
    typed = {k: v for k, v in typed.items() if v}
    if not typed.get("station"):
        return _picker(request, typed)
    platform = tt.clean_platform(typed["platform"]) if "platform" in typed else None
    if "platform" in typed and not platform:
        problem = (
            f"'{typed['platform']}' isn't a platform. Use its number or letter, like 9 or 10A."
        )
        return _picker(request, typed, problem, status=400)
    if platform and "to" in typed:
        problem = "A board is for a destination or a platform, not both. Clear one of them."
        return _picker(request, typed, problem, status=400)
    found: dict[str, stations.Station] = {}
    for field, heading in (("station", "Which station?"), ("to", "Going to which station?")):
        if field not in typed:
            continue
        match = tt.match(typed[field])
        if isinstance(match, stations.Station):
            found[field] = match
            continue
        if not match:
            problem = (
                f"No station matches '{typed[field]}'. Check the spelling, "
                "or use its three-letter code."
            )
            return _picker(request, typed, problem, status=404)
        return _picker(request, typed, choices=_choices(heading, match, field, typed))
    crs = found["station"].crs
    to = found["to"].crs if "to" in found and found["to"].crs != crs else None
    # #new tells site.js the board was made here, to count it once.
    return _see_other(tt.BoardKey(crs, to, platform).path + tt.NEW_BOARD)


@require_safe
def home(request: HttpRequest) -> HttpResponse:
    """The landing page, with Liverpool Street's live board under the lede."""
    name, _, title, description = pages.PAGES["/"]
    problem = ""
    try:
        data = runtime.run(shared().get, HOME_BOARD)
    except tt.BoardUnavailable as exc:
        # The page is still worth reading: the board says why it is empty,
        # and site.js fills it in when the next fetch works.
        data, problem = tt.unavailable(HOME_BOARD), str(exc)
    except TimeoutError:
        data, problem = tt.unavailable(HOME_BOARD), "The live board isn't answering."
    fields = _board_fields(HOME_BOARD, data, "home", problem)
    fields |= {"trains": fields["trains"][:HOME_ROWS], "rows": HOME_ROWS}
    context = page_context("/", title, description, "/") | fields
    response = render(request, "public/page.html", context | {"fragment": name})
    response["Cache-Control"] = "public, max-age=30"
    return response


@require_safe
def show(request: HttpRequest, station: str, then: str | None = None) -> HttpResponse:
    """A board, at its one address."""
    given = station[: stations.MAX_QUERY]
    then = then[: stations.MAX_QUERY] if then is not None else None
    key = tt.parse(given, then) or tt.loose(given, then)
    if key is None:
        # Not a board: the picker says why, or asks which station.
        query = {"station": given}
        if then:
            query[tt.then_field(then)] = then
        return _see_other(f"{tt.PICKER_PATH}?{urlencode(query)}")
    # One address for each board, so every share and embed of it is the same.
    if request.path != key.path:
        target = key.path + (f"?{request.META['QUERY_STRING']}" if request.GET else "")
        return redirect(target, permanent=True, preserve_request=True)
    if wait := _wait(request):
        return _refused(wait, HttpResponse(limited(wait), status=429, content_type="text/plain"))
    mode = _mode(request)
    try:
        data = runtime.run(shared().get, key)
    except tt.BoardUnavailable as exc:
        if mode == "page":
            typed_back = {"station": key.crs, "to": key.to or "", "platform": key.platform or ""}
            return _picker(request, typed_back, str(exc), status=503)
        # A TV or an embed has nobody to read a form: it shows the reason in
        # the board, and recovers by itself when the next fetch works.
        return _frame(request, key, tt.unavailable(key), mode, str(exc))
    if mode == "page":
        return _board_page(request, key, data)
    return _frame(request, key, data, mode)


def _feed_response(body: Any, status: int = 200, max_age: int = 30) -> JsonResponse:
    """JSON for site.js, kept by caches for ``max_age`` seconds."""
    response = JsonResponse(body, status=status, safe=False)
    response["Cache-Control"] = f"public, max-age={max_age}"
    return response


@require_safe
def feed(request: HttpRequest, station: str, then: str | None = None) -> JsonResponse:
    """The board as site.js fetches it each minute."""
    key = tt.parse(station, then)
    if key is None:
        return _feed_response({"error": "No such board."}, 404)
    if wait := _wait(request):
        return _refused(wait, JsonResponse({"error": limited(wait)}, status=429))  # type: ignore[return-value]
    try:
        minute, board = runtime.run(shared().get_for_minute, key)
    except tt.BoardUnavailable as exc:
        logger.warning("Board %s unavailable: %s", key.path, exc)
        return _feed_response({"error": tt.FEED_UNAVAILABLE}, 503)
    # No cache keeps it past its minute, even when the fetch ended after it.
    return _feed_response(board, max_age=int(tt.minute_left(minute)))


@require_safe
def suggest(request: HttpRequest) -> JsonResponse:
    """Station names for the picker's fields, as someone types."""
    query = request.GET.get("q", "")
    found = (
        [{"name": s.name, "crs": s.crs} for s, _ in stations.search(query, tt.MAX_SUGGESTIONS)]
        if len(query.strip()) >= 2
        else []
    )
    return _feed_response(found, max_age=86400)
