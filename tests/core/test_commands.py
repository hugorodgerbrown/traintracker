"""Tests for the purge_idempotency_records command."""

from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone
from freezegun import freeze_time

from apps.core.models import IdempotencyRecord

pytestmark = pytest.mark.django_db


def make_expired_record() -> None:
    """Create a record that expired an hour ago."""
    with freeze_time(timezone.now() - timedelta(days=2)):
        IdempotencyRecord.objects.reserve(
            key="old", method="POST", path="/", principal="", body_hash="", ttl=timedelta(days=1)
        )


def test_dry_run_by_default() -> None:
    """Without --commit nothing is deleted."""
    make_expired_record()
    out = StringIO()
    call_command("purge_idempotency_records", stdout=out)
    assert "1 expired records (dry run" in out.getvalue()
    assert IdempotencyRecord.objects.count() == 1


def test_commit_deletes_expired_rows_only() -> None:
    """--commit deletes expired rows and keeps live ones."""
    make_expired_record()
    IdempotencyRecord.objects.reserve(
        key="new", method="POST", path="/", principal="", body_hash="", ttl=timedelta(days=1)
    )
    out = StringIO()
    call_command("purge_idempotency_records", "--commit", stdout=out)
    assert "Deleted 1" in out.getvalue()
    assert list(IdempotencyRecord.objects.values_list("key", flat=True)) == ["new"]
