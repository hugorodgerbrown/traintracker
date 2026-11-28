"""Tests for apps.pairing.views: a TV asks, a phone types the code and a board, the TV collects."""

import re
from datetime import timedelta
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.pairing import services
from apps.pairing.models import TVPairing
from apps.public import board as tt
from tests.factories import SavedBoardFactory, UserFactory
from tests.public.fakes import BASE

pytestmark = pytest.mark.django_db

COL = tt.BoardKey("LST", "COL")


def _start(client: Client) -> dict[str, Any]:
    """Start a pairing as the TV app does and return its JSON."""
    response = client.post(reverse("pairing:start"))
    assert response.status_code == 201
    data: dict[str, Any] = response.json()
    return data


def _text(response: Any) -> str:
    """The page's HTML on one line."""
    return re.sub(r"\s+", " ", response.content.decode())


def test_a_tv_gets_its_board_end_to_end() -> None:
    """Start, type the code and pick a board on a phone, and the TV's next poll gets it."""
    tv, phone = Client(enforce_csrf_checks=True), Client()
    started = _start(tv)
    code = started["code"].replace("-", "")
    assert started["poll_seconds"] == 3
    assert started["pair_url"] == f"{BASE}/tv?code={code}"
    assert started["poll_url"].startswith(f"{BASE}/tv/pair/")
    waiting = tv.get(started["poll_url"].removeprefix(BASE))
    assert waiting.json()["status"] == "waiting"
    assert waiting["Cache-Control"] == "no-store"

    # Nobody signs in: boards are public.
    response = phone.post("/tv", {"code": started["code"].lower(), "board": COL.path})
    assert response.status_code == 302
    assert response["Location"] == "/tv?shown=%2Fboard%2FLST%2FCOL"
    done = _text(phone.get(response["Location"]))
    assert "<h1>It's on the TV</h1>" in done
    assert "The TV shows London Liverpool Street to Colchester in a few seconds." in done

    paired = tv.get(started["poll_url"].removeprefix(BASE)).json()
    assert paired == {
        "status": "paired",
        "board_name": "London Liverpool Street to Colchester",
        "board_url": f"{BASE}/board/LST/COL?tv=1",
    }
    # A reply lost on the way can be asked for again, until the pairing expires.
    assert tv.get(started["poll_url"].removeprefix(BASE)).json() == paired
    assert TVPairing.objects.get().delivered_at is not None
    TVPairing.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    assert tv.get(started["poll_url"].removeprefix(BASE)).status_code == 410


def test_the_code_reads_clearly() -> None:
    """Codes are six characters from an alphabet with no look-alikes, shown with a dash."""
    started = services.start()
    assert len(started.code) == services.CODE_LENGTH
    assert not set(started.code) & set("0O1IL")
    assert services.format_code("K7Q4MD") == "K7Q-4MD"


def test_only_hashes_are_stored() -> None:
    """Neither the code nor the token is in the row."""
    started = services.start()
    row = TVPairing.objects.get()
    assert started.code not in (row.code_hash, row.token_hash)
    assert started.token not in (row.code_hash, row.token_hash)


def test_unknown_token_is_404(client: Client) -> None:
    """A made-up token finds nothing."""
    assert client.get(reverse("pairing:poll", args=["nope"])).status_code == 404


def test_expired_pairing_is_410(client: Client) -> None:
    """A code nobody typed in time is gone."""
    started = services.start()
    TVPairing.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    response = client.get(reverse("pairing:poll", args=[started.token]))
    assert response.status_code == 410
    assert response.json() == {"status": "expired"}


def test_a_claim_whose_board_has_gone_hands_over_nothing(client: Client) -> None:
    """A claimed row whose address no longer names a board is expired to the TV."""
    started = services.start()
    TVPairing.objects.update(claimed_at=timezone.now(), path="/board/XYZ")
    assert client.get(reverse("pairing:poll", args=[started.token])).status_code == 410


def test_start_is_post_only_and_rate_limited(client: Client) -> None:
    """GET is refused; the twenty-first start in an hour from one address is too."""
    assert client.get(reverse("pairing:start")).status_code == 405
    for _ in range(20):
        _start(client)
    assert client.post(reverse("pairing:start")).status_code == 429


def test_the_page_fills_in_the_code_from_the_qr_link(client: Client) -> None:
    """Scanning the TV's QR code opens the page with the code already typed."""
    response = client.get("/tv", {"code": "K7Q4MD"})
    assert response.status_code == 200
    text = _text(response)
    assert 'value="K7Q4MD"' in text
    assert "<h1>Put a board on a TV</h1>" in text
    assert "no-store" in response["Cache-Control"]
    # No board chosen yet: saved_boards.js adds the saved ones, or it stays hidden.
    assert '<fieldset class="tv-boards" data-tv-boards hidden>' in text


def test_a_board_page_opens_the_page_with_its_board_chosen(client: Client) -> None:
    """From a board's page, its board is the one chosen."""
    text = _text(client.get("/tv", {"board": COL.path}))
    assert '<fieldset class="tv-boards" data-tv-boards>' in text
    assert (
        '<input type="radio" name="board" value="/board/LST/COL" checked /> '
        '<span>London Liverpool Street to Colchester <span class="hint">This board</span></span>'
    ) in text


def test_a_typed_station_wins_and_is_every_train() -> None:
    """Typing a station is enough, and names its board of every train."""
    started = services.start()
    phone = Client()
    response = phone.post("/tv", {"code": started.code, "board": COL.path, "station": "LST"})
    assert response.status_code == 302
    assert TVPairing.objects.get().path == "/board/LST"


def test_a_station_that_names_several_asks_which() -> None:
    """The candidates become boards to choose, and nothing is claimed."""
    started = services.start()
    response = Client().post("/tv", {"code": started.code, "station": "London"})
    assert response.status_code == 400
    text = _text(response)
    assert "More than one station matches &#x27;London&#x27;. Choose one." in text
    assert text.count('name="board"') > 1
    assert TVPairing.objects.get().claimed_at is None


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"code": "ABC", "board": "/board/LST"}, "A code is 6 letters and numbers"),
        ({"code": "ABCDEF", "board": "/board/LST"}, "isn&#x27;t waiting for a board"),
        ({"code": "ABCDEF"}, "Choose a board, or type a station."),
        ({"code": "ABCDEF", "board": "/board/XYZ"}, "No such board."),
        ({"code": "ABCDEF", "station": "Qqqqzzz"}, "No station matches &#x27;Qqqqzzz&#x27;"),
    ],
)
def test_a_wrong_code_or_board_says_so(client: Client, data: dict[str, str], message: str) -> None:
    """A malformed or unknown code, or no board, is refused with a reason."""
    response = client.post("/tv", data)
    assert response.status_code == 400
    assert message in response.content.decode()


def test_a_refused_claim_keeps_the_board_chosen(client: Client) -> None:
    """Sent back with a problem, the board chosen is still chosen."""
    text = _text(client.post("/tv", {"code": "ABCDEF", "board": COL.path}))
    assert 'value="/board/LST/COL" checked' in text
    assert '<span class="hint">Your choice</span>' in text


def test_a_code_works_once(client: Client) -> None:
    """Once claimed, the same code can't be claimed again."""
    started = services.start()
    assert services.claim(started.code, COL) is not None
    assert client.post("/tv", {"code": started.code, "board": COL.path}).status_code == 400


def test_an_expired_code_cant_be_claimed() -> None:
    """A code past its ten minutes gives nothing a board."""
    started = services.start()
    TVPairing.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    assert services.claim(started.code, COL) is None
    assert services.claim("short", COL) is None


def test_collect_needs_a_claim() -> None:
    """An unclaimed pairing hands over nothing."""
    assert services.collect(services.start().pairing) is None


def test_guesses_are_rate_limited(client: Client) -> None:
    """The eleventh try in ten minutes from one address is refused, even with a good code."""
    for _ in range(10):
        client.post("/tv", {"code": "ABCDEF", "board": COL.path})
    started = services.start()
    response = client.post("/tv", {"code": started.code, "board": COL.path})
    assert response.status_code == 429
    assert "Too many tries." in response.content.decode()
    assert TVPairing.objects.get().claimed_at is None


def test_claiming_gives_the_tv_a_fresh_ten_minutes() -> None:
    """A code claimed in its last second still leaves the TV time to collect it."""
    started = services.start()
    TVPairing.objects.update(expires_at=timezone.now() + timedelta(seconds=1))
    pairing = services.claim(started.code, COL)
    assert pairing is not None
    assert pairing.expires_at > timezone.now() + timedelta(minutes=9)


def test_signed_in_or_not_is_the_same(client: Client) -> None:
    """A signed-in person's saved boards come from saved_boards.js, not the page."""
    user = UserFactory.create()
    SavedBoardFactory.create(user=user, path=COL.path, favourite=True, favourited_at=timezone.now())
    client.force_login(user)
    assert "data-tv-boards hidden" in _text(client.get("/tv"))
