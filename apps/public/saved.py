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

from django.contrib.auth import get_user_model
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
NOT_JSON = "Send a JSON object."
NOT_TRUE_OR_FALSE = "Say whether it is a favourite: true or false."
TOO_MANY = f"Send at most {SavedBoard.MAX_FAVOURITES} favourites."


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


def _lock(user: AbstractBaseUser) -> None:
    """Hold the account's row until the transaction ends.

    Two devices saving at once then take turns, so ``_tidy`` never counts
    favourites another is still changing and the limit holds.
    """
    list(get_user_model().objects.select_for_update().filter(pk=user.pk).values_list("pk"))


def opened(user: AbstractBaseUser, key: tt.BoardKey) -> None:
    """Remember ``key`` as the last board ``user`` opened."""
    with transaction.atomic():
        _lock(user)
        row, _ = SavedBoard.objects.get_or_create(user_id=user.pk, path=key.path)
        row.opened_at = timezone.now()
        row.save(update_fields=["opened_at", "updated_at"])
        _tidy(user)


def favour(user: AbstractBaseUser, key: tt.BoardKey, on: bool) -> None:
    """Make ``key`` one of ``user``'s favourites, or stop it being one.

    A new favourite past the limit pushes out the oldest.
    """
    with transaction.atomic():
        _lock(user)
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
        _lock(user)
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


def _key(path: Any) -> tt.BoardKey | None:
    """The board a posted address names, or None."""
    return tt.from_path(path) if isinstance(path, str) else None


def _posted(request: HttpRequest) -> dict[str, Any] | None:
    """The JSON object posted, or None if the body isn't one."""
    try:
        body = json.loads(request.body)
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def _refused(error: str) -> JsonResponse:
    """A 400 saying why. ``error`` is one of this module's own messages."""
    return JsonResponse({"error": error}, status=400)


def _answer(request: HttpRequest) -> JsonResponse:
    """The account's boards now."""
    return JsonResponse(state(signed_in_user(request)))


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
    body = _posted(request)
    if body is None:
        return _refused(NOT_JSON)
    key = _key(body.get("path"))
    if key is None:
        return _refused(NOT_A_BOARD)
    opened(signed_in_user(request), key)
    return _answer(request)


@never_cache
@login_required_json
@require_POST
def boards_favourite(request: HttpRequest) -> JsonResponse:
    """Add or remove a favourite: ``{"path": "/board/LST", "favourite": true}``."""
    body = _posted(request)
    if body is None:
        return _refused(NOT_JSON)
    on = body.get("favourite")
    if not isinstance(on, bool):
        return _refused(NOT_TRUE_OR_FALSE)
    key = _key(body.get("path"))
    if key is None:
        return _refused(NOT_A_BOARD)
    favour(signed_in_user(request), key, on)
    return _answer(request)


@never_cache
@login_required_json
@require_POST
def boards_move(request: HttpRequest) -> JsonResponse:
    """Add a browser's boards to the account: ``{"last": path or null, "favourites": [path]}``.

    Addresses that no longer name a board are skipped, not refused: the browser
    may have kept them from an older version of the site.
    """
    body = _posted(request)
    if body is None:
        return _refused(NOT_JSON)
    paths = body.get("favourites", [])
    if not isinstance(paths, list) or len(paths) > SavedBoard.MAX_FAVOURITES:
        return _refused(TOO_MANY)
    favourites = [k for p in paths if (k := _key(p))]
    move(signed_in_user(request), _key(body.get("last")), favourites)
    return _answer(request)
