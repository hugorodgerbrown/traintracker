"""Views for pairing: the TV app's two JSON endpoints and the phone's /tv page.

The TV's endpoints need no sign-in and carry no cookies, so starting a
pairing is exempt from CSRF: there is no session for a forged request to
ride on, and all it makes is a code that expires in minutes. The phone's
page needs no sign-in either: boards are public, so a code only ever puts a
public board on the TV that showed it
(docs/decisions/a-tv-picks-its-board-from-a-phone.md).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.core.ratelimit import client_ip, over_limit
from apps.core.shortcuts import render
from apps.pairing import services
from apps.pairing.forms import PairForm
from apps.public import board as tt
from apps.public.views import page_context
from apps.rail import stations

# How often the TV asks whether its code has been typed, in seconds.
POLL_SECONDS = 3
TV_PATH = "/tv"
TITLE = "Put a board on a TV"
DESCRIPTION = "Type the code the Traintrackr TV app shows, and choose the board it shows."
NOT_WAITING = (
    "That code isn't waiting for a board. Check the TV: codes last "
    f"{services.PAIRING_MINUTES} minutes and work once."
)
TOO_MANY = "Too many tries. Wait a few minutes, then try again."


def _json(data: dict[str, object], status: int = 200) -> JsonResponse:
    """Return ``data`` as JSON that nothing between here and the TV may keep."""
    response = JsonResponse(data, status=status)
    response["Cache-Control"] = "no-store"
    return response


# The TV sends no cookies, so there is no session for a forged request to use
# (docs/decisions/a-tv-picks-its-board-from-a-phone.md).
@csrf_exempt  # nosemgrep: python.django.security.audit.csrf-exempt.no-csrf-exempt
@require_POST
def tv_start(request: HttpRequest) -> HttpResponse:
    """Start a pairing: the code to show, the link for its QR code and where to poll."""
    if over_limit("pairing-start-ip", client_ip(request), limit=20, window_seconds=3600):
        return _json({"error": "rate_limited"}, status=429)
    started = services.start()
    return _json(
        {
            "code": services.format_code(started.code),
            "pair_url": f"{settings.SITE_URL}{TV_PATH}?{urlencode({'code': started.code})}",
            "poll_url": settings.SITE_URL + reverse("pairing:poll", args=[started.token]),
            "poll_seconds": POLL_SECONDS,
            "expires_at": started.pairing.expires_at.isoformat(),
        },
        status=201,
    )


@require_GET
def tv_poll(request: HttpRequest, token: str) -> HttpResponse:
    """Tell the TV whether its code has been typed, and if so, its board's TV address."""
    pairing = services.find(token)
    if pairing is None:
        return _json({"status": "unknown"}, status=404)
    if pairing.claimed_at is None:
        if pairing.is_expired():
            return _json({"status": "expired"}, status=410)
        return _json({"status": "waiting", "expires_at": pairing.expires_at.isoformat()})
    key = services.collect(pairing)
    if key is None:
        return _json({"status": "expired"}, status=410)
    return _json(
        {
            "status": "paired",
            "board_name": key.name(),
            "board_url": f"{settings.SITE_URL}{key.path}?tv=1",
        }
    )


def _option(key: tt.BoardKey, note: str, checked: bool = False) -> dict[str, Any]:
    """One board to choose, as the template draws it."""
    return {"path": key.path, "name": key.name(), "note": note, "checked": checked}


def _chosen(form: PairForm) -> tuple[tt.BoardKey | None, list[dict[str, Any]], str]:
    """The board the form names; or the stations to choose from, or why none.

    A typed station wins over a chosen board, and names its every-train board.
    """
    typed = form.cleaned_data.get("station", "")
    if typed:
        match = tt.match(typed)
        if isinstance(match, stations.Station):
            return tt.BoardKey(match.crs), [], ""
        if not match:
            problem = (
                f"No station matches '{typed}'. Check the spelling, or use its three-letter code."
            )
            return None, [], problem
        options = [_option(tt.BoardKey(s.crs), s.crs) for s in match]
        return None, options, f"More than one station matches '{typed}'. Choose one."
    key = tt.from_path(form.cleaned_data.get("board", ""))
    return key, [], "" if key else "No such board. Choose another, or type a station."


@never_cache
@require_http_methods(["GET", "POST"])
def pair(request: HttpRequest) -> HttpResponse:
    """The phone's page: type the TV's code, choose a board, and the TV shows it."""
    status = 200
    problem = ""
    options: list[dict[str, Any]] = []
    if request.method == "POST":
        form = PairForm(request.POST)
        if over_limit("pairing-claim-ip", client_ip(request), limit=10, window_seconds=600):
            problem, status = TOO_MANY, 429
        elif form.is_valid():
            key, options, problem = _chosen(form)
            if key and services.claim(form.cleaned_data["code"], key):
                return redirect(f"{TV_PATH}?{urlencode({'shown': key.path})}")
            if key:
                form.add_error("code", NOT_WAITING)
            status = 400
        else:
            status = 400
        given = tt.from_path(request.POST.get("board", ""))
    else:
        form = PairForm(initial={"code": request.GET.get("code", "")[:20]})
        given = tt.from_path(request.GET.get("board", ""))
    if given and not options:
        # From a board's page it is "this board"; sent back with a problem, it is the one chosen.
        note = "This board" if request.method == "GET" else "Your choice"
        options = [_option(given, note, checked=True)]
    shown = tt.from_path(request.GET.get("shown", "")) if request.method == "GET" else None
    context = page_context(TV_PATH, TITLE, DESCRIPTION, TV_PATH) | {
        "form": form,
        "options": options,
        "problem": problem,
        "shown": shown.name() if shown else "",
        "max_query": stations.MAX_QUERY,
        "minutes": services.PAIRING_MINUTES,
    }
    return render(request, "pairing/tv.html", context, status=status)
