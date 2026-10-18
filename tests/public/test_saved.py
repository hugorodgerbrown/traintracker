"""Saved boards: the model, what the account keeps, /api/boards and the pages' hooks."""

import json
import re
from datetime import timedelta
from typing import Any

import pytest
from django.db import IntegrityError, connection, transaction
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.public import board as tt
from apps.public import saved
from apps.public.models import SavedBoard
from tests.factories import SavedBoardFactory, UserFactory

pytestmark = pytest.mark.django_db

LST = tt.BoardKey("LST")
COL = tt.BoardKey("LST", "COL")


def _post(client: Client, name: str, body: Any) -> Any:
    """POST ``body`` as JSON to the saved-boards endpoint ``name``."""
    return client.post(reverse(f"public:{name}"), json.dumps(body), content_type="application/json")


def _paths(boards: list[dict[str, str]]) -> list[str]:
    return [b["path"] for b in boards]


# ------------------------------------------------------------------ the model


def test_a_saved_board_is_named_as_its_board() -> None:
    """to_string() is the board's short name, or the address if it names none now."""
    assert SavedBoardFactory.create(path="/board/LST/COL").to_string() == (
        "London Liverpool Street to Colchester"
    )
    assert str(SavedBoardFactory.create(path="/board/XXX")) == "/board/XXX"


def test_saved_boards_are_kept_once_per_account_and_go_with_it(user: Any) -> None:
    """One row a board for each account; deleting the account deletes its boards."""
    SavedBoardFactory.create(user=user)
    SavedBoardFactory.create(path="/board/LST")  # someone else's
    with pytest.raises(IntegrityError), transaction.atomic():
        SavedBoardFactory.create(user=user)
    user.delete()
    assert SavedBoard.objects.count() == 1


def test_the_queryset_finds_favourites_oldest_first_and_the_last_opened(user: Any) -> None:
    """favourites() in the order added; opened() the latest first."""
    now = timezone.now()
    SavedBoardFactory.create(user=user, path="/board/CBG", favourite=True, favourited_at=now)
    SavedBoardFactory.create(
        user=user,
        path="/board/LST",
        favourite=True,
        favourited_at=now - timedelta(days=1),
        opened_at=now - timedelta(hours=1),
    )
    boards = SavedBoard.objects.for_user(user)
    assert [b.path for b in boards.favourites()] == ["/board/LST", "/board/CBG"]
    assert boards.opened().first().path == "/board/CBG"  # type: ignore[union-attr]


# --------------------------------------------------------------- the account


def test_opening_a_board_keeps_only_the_last_one_and_the_favourites(user: Any) -> None:
    """A board opened replaces the last one, unless that one is a favourite."""
    saved.opened(user, LST)
    saved.opened(user, COL)
    assert saved.state(user) == {
        "last": {"path": "/board/LST/COL", "name": "London Liverpool Street to Colchester"},
        "favourites": [],
    }
    saved.favour(user, COL, on=True)
    saved.opened(user, LST)
    assert SavedBoard.objects.for_user(user).count() == 2
    assert saved.state(user)["last"]["path"] == "/board/LST"


def test_favourites_are_added_once_removed_and_capped(user: Any) -> None:
    """The 13th favourite pushes out the oldest; removing one keeps no row it doesn't need."""
    keys = [tt.BoardKey(crs) for crs in ("LST", "CBG", "COL", "IPS", "NRW", "MKT", "SUY")]
    keys += [tt.BoardKey("LST", platform=str(n)) for n in range(1, 7)]
    for key in keys:
        saved.favour(user, key, on=True)
    saved.favour(user, keys[-1], on=True)  # again: no change
    favourites = _paths(saved.state(user)["favourites"])
    assert favourites == [k.path for k in keys[1:]]
    saved.favour(user, keys[1], on=False)
    assert keys[1].path not in _paths(saved.state(user)["favourites"])
    assert SavedBoard.objects.for_user(user).count() == SavedBoard.MAX_FAVOURITES - 1


def test_moving_a_browsers_boards_adds_them_without_pushing_any_out(user: Any) -> None:
    """The browser's favourites join while there's room; its last board only if there's none."""
    saved.move(user, COL, [LST, COL])
    state = saved.state(user)
    assert _paths(state["favourites"]) == ["/board/LST", "/board/LST/COL"]
    assert state["last"]["path"] == "/board/LST/COL"

    saved.move(user, LST, [COL, tt.BoardKey("CBG")])  # COL already a favourite
    state = saved.state(user)
    assert _paths(state["favourites"]) == ["/board/LST", "/board/LST/COL", "/board/CBG"]
    assert state["last"]["path"] == "/board/LST/COL"  # the account's own last board stays

    full = UserFactory.create()
    mine = [tt.BoardKey("LST", platform=str(n)) for n in range(1, 13)]
    for key in mine:
        saved.favour(full, key, on=True)
    saved.move(full, None, [tt.BoardKey("CBG")])
    assert _paths(saved.state(full)["favourites"]) == [k.path for k in mine]


def test_saving_locks_the_account_first(user: Any) -> None:
    """Two devices saving at once take turns, so the favourites never pass the limit.

    The lock is the transaction's first query; SQLite (the test database) has
    no FOR UPDATE and serialises writes itself, so only Postgres shows it.
    """
    for save in (lambda: saved.opened(user, LST), lambda: saved.favour(user, LST, on=True)):
        with CaptureQueriesContext(connection) as queries:
            save()
        sql = [q["sql"] for q in queries.captured_queries if "SAVEPOINT" not in q["sql"]]
        assert sql[0].startswith('SELECT "accounts_user"."id"')
        assert ("FOR UPDATE" in sql[0]) == connection.features.has_select_for_update


# ------------------------------------------------------------- /api/boards


def test_signed_out_the_browser_keeps_the_boards(client: Client) -> None:
    """GET says so (a 200, so the console stays clean); every write is a 401."""
    response = client.get(reverse("public:saved_boards"))
    assert response.json() == {"signed_in": False}
    assert "no-store" in response["Cache-Control"]
    for name in ("saved_boards_opened", "saved_boards_favourite", "saved_boards_move"):
        refused = _post(client, name, {"path": "/board/LST"})
        assert refused.status_code == 401, name
        assert "no-store" in refused["Cache-Control"], name


def test_signed_in_the_account_keeps_them(signed_in: Client) -> None:
    """GET gives the boards and the token posts carry; each post answers with the boards."""
    response = signed_in.get(reverse("public:saved_boards"))
    body = response.json()
    assert body["signed_in"] is True
    assert body["last"] is None
    assert body["favourites"] == []
    assert body["csrf"]
    assert "no-store" in response["Cache-Control"]
    assert "private" in response["Cache-Control"]

    assert _post(signed_in, "saved_boards_opened", {"path": "/board/LST/COL"}).json()["last"] == {
        "path": "/board/LST/COL",
        "name": "London Liverpool Street to Colchester",
    }
    on = _post(signed_in, "saved_boards_favourite", {"path": "/board/CBG/1", "favourite": True})
    assert on.json()["favourites"] == [{"path": "/board/CBG/1", "name": "Cambridge, platform 1"}]
    off = _post(signed_in, "saved_boards_favourite", {"path": "/board/CBG/1", "favourite": False})
    assert off.json()["favourites"] == []


def test_posts_need_the_token(user: Any) -> None:
    """A post without the CSRF token GET gave is refused."""
    client = Client(enforce_csrf_checks=True)
    client.force_login(user)
    assert _post(client, "saved_boards_opened", {"path": "/board/LST"}).status_code == 403
    token = client.get(reverse("public:saved_boards")).json()["csrf"]
    response = client.post(
        reverse("public:saved_boards_opened"),
        json.dumps({"path": "/board/LST"}),
        content_type="application/json",
        headers={"X-CSRFToken": token},
    )
    assert response.status_code == 200


@pytest.mark.parametrize(
    ("name", "body", "error"),
    [
        ("saved_boards_opened", {"path": "/board/lst"}, saved.NOT_A_BOARD),
        ("saved_boards_opened", {"path": "https://example.com/board/LST"}, saved.NOT_A_BOARD),
        ("saved_boards_opened", {}, saved.NOT_A_BOARD),
        ("saved_boards_opened", ["/board/LST"], saved.NOT_JSON),
        ("saved_boards_favourite", {"path": "/board/LST"}, "true or false"),
        ("saved_boards_favourite", {"path": "/board/XXX", "favourite": True}, saved.NOT_A_BOARD),
        ("saved_boards_move", {"favourites": "/board/LST"}, "at most 12"),
        ("saved_boards_move", {"favourites": ["/board/LST"] * 13}, "at most 12"),
    ],
)
def test_what_isnt_a_board_is_refused(signed_in: Client, name: str, body: Any, error: str) -> None:
    """Only a board's own address is kept; anything else is a 400 saying why."""
    response = _post(signed_in, name, body)
    assert response.status_code == 400
    assert error in response.json()["error"]
    assert not SavedBoard.objects.exists()


def test_a_body_that_isnt_json_is_refused(signed_in: Client) -> None:
    """Text that isn't JSON is a 400."""
    response = signed_in.post(
        reverse("public:saved_boards_opened"), "path=/board/LST", content_type="text/plain"
    )
    assert response.status_code == 400


def test_moving_skips_addresses_that_no_longer_name_a_board(signed_in: Client) -> None:
    """A browser may have kept an old address; it is left out, not refused."""
    body = {"last": "/board/XXX", "favourites": ["/board/XXX", "/board/LST", 7]}
    response = _post(signed_in, "saved_boards_move", body)
    assert response.json() == {
        "last": None,
        "favourites": [{"path": "/board/LST", "name": "London Liverpool Street"}],
    }


def test_the_endpoints_take_only_their_method(signed_in: Client) -> None:
    """GET reads, the rest post."""
    assert signed_in.post(reverse("public:saved_boards")).status_code == 405
    assert signed_in.get(reverse("public:saved_boards_opened")).status_code == 405


# ---------------------------------------------------------------- the pages


def test_a_board_page_carries_what_saved_boards_js_needs(client: Client) -> None:
    """Its address and short name, and a button hidden until the script shows it."""
    page = client.get("/board/CBG/1").content.decode()
    box = re.search(r'<div class="favourite"[^>]*>', page)
    assert box is not None
    assert 'data-board-path="/board/CBG/1"' in box.group()
    assert 'data-board-name="Cambridge, platform 1"' in box.group()
    assert " hidden" in box.group()
    assert '<script src="/static/js/saved_boards.js" defer></script>' in page


def test_embeds_and_tvs_keep_nothing(client: Client) -> None:
    """Only the board's own page is remembered: a frame has no script for it."""
    for address in ("/board/LST?embed=1", "/board/LST?tv=1"):
        page = client.get(address).content.decode()
        assert "saved_boards.js" not in page, address
        assert "data-board-path" not in page, address


def test_the_front_page_is_the_same_for_everyone(client: Client, signed_in_other: Client) -> None:
    """The boards are drawn by the browser, so the page stays publicly cached."""
    anonymous = client.get("/")
    assert anonymous["Cache-Control"] == "public, max-age=300"
    page = anonymous.content.decode()
    assert '<figure class="departures" data-example>' in page
    assert '<template id="your-board">' in page
    assert re.search(r'<section class="your-favourites"[^>]*hidden>', page)
    mine = signed_in_other.get("/")
    assert mine["Cache-Control"] == "public, max-age=300"
    main = re.compile(rb"<main.*</main>", re.DOTALL)
    assert main.search(mine.content).group() == main.search(anonymous.content).group()  # type: ignore[union-attr]


@pytest.fixture
def signed_in_other(user: Any) -> Client:
    """A second client, signed in, beside the anonymous ``client``."""
    other = Client()
    SavedBoardFactory.create(user=user, favourite=True, favourited_at=timezone.now())
    other.force_login(user)
    return other


# ------------------------------------------------------------------ the admin


def test_staff_can_look_at_saved_boards_but_not_change_them(client: Client) -> None:
    """The admin lists them; it can't add or edit one."""
    client.force_login(UserFactory.create(is_staff=True, is_superuser=True))
    board = SavedBoardFactory.create(path="/board/LST/COL")
    listing = client.get(reverse("admin:public_savedboard_changelist")).content.decode()
    assert "/board/LST/COL" in listing
    assert client.get(reverse("admin:public_savedboard_add")).status_code == 403
    page = client.get(reverse("admin:public_savedboard_change", args=[board.pk]))
    assert page.status_code == 200
    assert 'name="_save"' not in page.content.decode()
