"""A signed-in person's saved boards: the last one they opened, and their favourites.

static/js/saved_boards.js asks ``/api/boards`` first. Signed out, it answers
``{"signed_in": false}`` and the browser keeps the boards itself; signed in,
these endpoints keep them with the account, and the browser keeps nothing.
At sign-in, what that browser had kept is moved here once (``move``).

Every answer is ``private, no-store``: it is one person's.
"""

import json
import logging
from typing import Any

from django.contrib.auth.base_user import AbstractBaseUser
from django.db import transaction
from django.http import HttpRequest, JsonResponse
from django.middleware.csrf import get_token
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from apps.core.decorators import login_required_json, signed_in_user
from apps.public import board as tt
from apps.public.models import SavedBoard

logger = logging.getLogger(__name__)

NOT_A_BOARD = "No such board."


class BadRequest(Exception):
    """What was posted isn't what the endpoint takes. The message is safe to show."""


def _board(path: str) -> dict[str, str]:
    """A saved board as the browser draws it: its address and short name."""
    key = tt.from_path(path)
    return {"path": path, "name": key.name() if key else path}


def state(user: AbstractBaseUser) -> dict[str, Any]:
    """The account's last board (or None) and its favourites, oldest first."""
    boards = SavedBoard.objects.for_user(user)
    last = boards.opened().first()
    return {
        "last": _board(last.path) if last else None,
        "favourites": [_board(b.path) for b in boards.favourites()],
    }


def _tidy(user: AbstractBaseUser) -> None:
    """Keep the newest ``MAX_FAVOURITES`` favourites, and drop rows nothing needs.

    A row is needed while it is a favourite or the last board opened.
    """
    boards = SavedBoard.objects.for_user(user)
    extra = list(boards.favourites().values_list("id", flat=True))[: -SavedBoard.MAX_FAVOURITES]
    if extra:
        boards.filter(id__in=extra).update(favourite=False, favourited_at=None)
    unneeded = boards.filter(favourite=False)
    if (last := boards.opened().values_list("id", flat=True).first()) is not None:
        unneeded = unneeded.exclude(id=last)
    unneeded.delete()


def opened(user: AbstractBaseUser, key: tt.BoardKey) -> None:
    """Remember ``key`` as the last board ``user`` opened."""
    with transaction.atomic():
        row, _ = SavedBoard.objects.get_or_create(user_id=user.pk, path=key.path)
        row.opened_at = timezone.now()
        row.save(update_fields=["opened_at", "updated_at"])
        _tidy(user)


def favour(user: AbstractBaseUser, key: tt.BoardKey, on: bool) -> None:
    """Make ``key`` one of ``user``'s favourites, or stop it being one.

    A new favourite past the limit pushes out the oldest.
    """
    with transaction.atomic():
        row, _ = SavedBoard.objects.get_or_create(user_id=user.pk, path=key.path)
        if on != row.favourite:
            row.favourite = on
            row.favourited_at = timezone.now() if on else None
            row.save(update_fields=["favourite", "favourited_at", "updated_at"])
        _tidy(user)


def move(user: AbstractBaseUser, last: tt.BoardKey | None, favourites: list[tt.BoardKey]) -> None:
    """Add what a browser kept before its owner signed in to their account.

    The browser's favourites join the account's, in their order, while there
    is room: they never push out one the account already has. The browser's
    last board becomes the account's only if the account has none.
    """
    with transaction.atomic():
        boards = SavedBoard.objects.for_user(user)
        room = SavedBoard.MAX_FAVOURITES - boards.favourites().count()
        for key in favourites:
            if room <= 0:
                break
            if not boards.filter(path=key.path, favourite=True).exists():
                favour(user, key, on=True)
                room -= 1
        if last and not boards.opened().exists():
            opened(user, last)


def _key(path: Any) -> tt.BoardKey:
    """The board a posted address names, or BadRequest."""
    key = tt.from_path(path) if isinstance(path, str) else None
    if key is None:
        raise BadRequest(NOT_A_BOARD)
    return key


def _posted(request: HttpRequest) -> dict[str, Any]:
    """The JSON object posted, or BadRequest."""
    try:
        body = json.loads(request.body)
    except ValueError as exc:
        raise BadRequest("Send a JSON object.") from exc
    if not isinstance(body, dict):
        raise BadRequest("Send a JSON object.")
    return body


def _answer(request: HttpRequest, status: int = 200, error: str = "") -> JsonResponse:
    """The account's boards now, or why the request was refused."""
    if error:
        return JsonResponse({"error": error}, status=status)
    return JsonResponse(state(signed_in_user(request)), status=status)


@never_cache
@require_GET
def boards(request: HttpRequest) -> JsonResponse:
    """The account's boards, and the token its posts to these endpoints carry.

    Signed out, only ``{"signed_in": false}``: the browser keeps the boards.
    That is a 200, not a 401, as every signed-out visit to the front page or
    a board asks, and a 401 would show as an error in the browser's console.
    """
    if not request.user.is_authenticated:
        return JsonResponse({"signed_in": False})
    user = signed_in_user(request)
    return JsonResponse({"signed_in": True} | state(user) | {"csrf": get_token(request)})


@never_cache
@login_required_json
@require_POST
def boards_opened(request: HttpRequest) -> JsonResponse:
    """Remember the board posted (``{"path": "/board/LST"}``) as the last one opened."""
    try:
        opened(signed_in_user(request), _key(_posted(request).get("path")))
    except BadRequest as exc:
        return _answer(request, 400, str(exc))
    return _answer(request)


@never_cache
@login_required_json
@require_POST
def boards_favourite(request: HttpRequest) -> JsonResponse:
    """Add or remove a favourite: ``{"path": "/board/LST", "favourite": true}``."""
    try:
        body = _posted(request)
        if not isinstance(body.get("favourite"), bool):
            raise BadRequest("Say whether it is a favourite: true or false.")
        favour(signed_in_user(request), _key(body.get("path")), body["favourite"])
    except BadRequest as exc:
        return _answer(request, 400, str(exc))
    return _answer(request)


@never_cache
@login_required_json
@require_POST
def boards_move(request: HttpRequest) -> JsonResponse:
    """Add a browser's boards to the account: ``{"last": path or null, "favourites": [path]}``.

    Addresses that no longer name a board are skipped, not refused: the browser
    may have kept them from an older version of the site.
    """
    try:
        body = _posted(request)
        paths = body.get("favourites", [])
        if not isinstance(paths, list) or len(paths) > SavedBoard.MAX_FAVOURITES:
            raise BadRequest(f"Send at most {SavedBoard.MAX_FAVOURITES} favourites.")
    except BadRequest as exc:
        return _answer(request, 400, str(exc))
    favourites = [k for p in paths if isinstance(p, str) and (k := tt.from_path(p))]
    last = body.get("last")
    move(signed_in_user(request), tt.from_path(last) if isinstance(last, str) else None, favourites)
    return _answer(request)
