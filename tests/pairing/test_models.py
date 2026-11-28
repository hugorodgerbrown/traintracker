"""Tests for apps.pairing.models and its admin."""

from datetime import timedelta
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.pairing.models import TVPairing
from apps.public import board as tt
from tests.factories import TVPairingFactory, UserFactory

pytestmark = pytest.mark.django_db


def test_live_and_expired() -> None:
    """Live is unclaimed and unexpired; expired is past its time, claimed or not."""
    waiting = TVPairingFactory.create()
    claimed = TVPairingFactory.create(claimed_at=timezone.now())
    old = TVPairingFactory.create(expires_at=timezone.now() - timedelta(seconds=1))
    assert list(TVPairing.objects.live()) == [waiting]
    assert list(TVPairing.objects.expired()) == [old]
    assert claimed not in TVPairing.objects.live()


@pytest.mark.parametrize(
    ("fields", "state"),
    [
        ({}, "waiting"),
        ({"expires_at": timezone.now() - timedelta(minutes=1)}, "expired"),
        ({"claimed_at": timezone.now()}, "claimed"),
        ({"claimed_at": timezone.now(), "delivered_at": timezone.now()}, "delivered"),
    ],
)
def test_to_string(fields: dict[str, Any], state: str) -> None:
    """The label says how far the pairing got."""
    assert str(TVPairingFactory.create(**fields)) == f"TV pairing ({state})"


def test_board_is_the_claimed_address() -> None:
    """Unclaimed, there is no board; claimed, it is the address's board."""
    assert TVPairingFactory.create().board() is None
    assert TVPairingFactory.create(path="/board/LST/COL").board() == tt.BoardKey("LST", "COL")


def test_ordering_is_newest_first() -> None:
    """The admin lists the newest pairing first."""
    first, second = TVPairingFactory.create(), TVPairingFactory.create()
    assert list(TVPairing.objects.all()) == [second, first]


def test_admin_lists_pairings_and_cannot_add(client: Client) -> None:
    """Staff can see pairings but not make one."""
    client.force_login(UserFactory.create(is_staff=True, is_superuser=True))
    pairing = TVPairingFactory.create(path="/board/LST")
    assert client.get(reverse("admin:pairing_tvpairing_changelist")).status_code == 200
    change = reverse("admin:pairing_tvpairing_change", args=[pairing.pk])
    assert client.get(change).status_code == 200
    assert client.get(reverse("admin:pairing_tvpairing_add")).status_code == 403
