"""Tests for the purge_sign_in_requests command."""

from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.accounts.models import SignInRequest
from tests.factories import SignInRequestFactory

pytestmark = pytest.mark.django_db


def test_purge_is_a_dry_run_by_default() -> None:
    """Without --commit nothing is deleted."""
    SignInRequestFactory.create(expires_at=timezone.now() - timedelta(minutes=1))
    out = StringIO()
    call_command("purge_sign_in_requests", stdout=out)
    assert "1 expired" in out.getvalue()
    assert SignInRequest.objects.count() == 1


def test_purge_deletes_only_expired_requests() -> None:
    """--commit deletes the expired rows and keeps live ones."""
    SignInRequestFactory.create(expires_at=timezone.now() - timedelta(minutes=1))
    live = SignInRequestFactory.create()
    out = StringIO()
    call_command("purge_sign_in_requests", "--commit", stdout=out)
    assert "Deleted 1" in out.getvalue()
    assert list(SignInRequest.objects.all()) == [live]
