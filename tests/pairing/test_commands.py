"""Tests for the purge_tv_pairings management command."""

from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.pairing.models import TVPairing
from tests.factories import TVPairingFactory

pytestmark = pytest.mark.django_db


def test_dry_run_counts_and_keeps() -> None:
    """Without --commit nothing is deleted."""
    TVPairingFactory.create(expires_at=timezone.now() - timedelta(minutes=1))
    out = StringIO()
    call_command("purge_tv_pairings", stdout=out)
    assert "1 expired TV pairings (dry run" in out.getvalue()
    assert TVPairing.objects.count() == 1


def test_commit_deletes_only_expired() -> None:
    """--commit deletes expired pairings and keeps live ones."""
    TVPairingFactory.create(expires_at=timezone.now() - timedelta(minutes=1))
    live = TVPairingFactory.create()
    out = StringIO()
    call_command("purge_tv_pairings", "--commit", stdout=out)
    assert "Deleted 1 expired TV pairings." in out.getvalue()
    assert list(TVPairing.objects.all()) == [live]


def test_commit_is_quiet_at_verbosity_zero() -> None:
    """--verbosity 0 says nothing."""
    out = StringIO()
    call_command("purge_tv_pairings", "--commit", verbosity=0, stdout=out)
    assert out.getvalue() == ""
