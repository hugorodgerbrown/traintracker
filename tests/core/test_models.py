"""Tests for apps.core.models."""

from datetime import timedelta

import pytest
from django.utils import timezone
from freezegun import freeze_time

from apps.core.models import IdempotencyRecord

FINGERPRINT = {"method": "POST", "path": "/x", "principal": "1", "body_hash": "h"}

pytestmark = pytest.mark.django_db


def test_reserve_claims_a_fresh_key() -> None:
    """The first claim wins and the row is reserved."""
    record, claimed = IdempotencyRecord.objects.reserve(
        key="k", ttl=timedelta(hours=1), **FINGERPRINT
    )
    assert claimed
    assert record is not None
    assert not record.is_completed()


def test_reserve_reports_a_live_holder() -> None:
    """A second claim on a live key loses and sees the holder's row."""
    first, _ = IdempotencyRecord.objects.reserve(key="k", ttl=timedelta(hours=1), **FINGERPRINT)
    second, claimed = IdempotencyRecord.objects.reserve(
        key="k", ttl=timedelta(hours=1), **FINGERPRINT
    )
    assert not claimed
    assert second == first


def test_reserve_takes_over_an_expired_key() -> None:
    """An expired key can be claimed again, with the new fingerprint."""
    IdempotencyRecord.objects.reserve(key="k", ttl=timedelta(hours=1), **FINGERPRINT)
    with freeze_time(timezone.now() + timedelta(hours=2)):
        record, claimed = IdempotencyRecord.objects.reserve(
            key="k", ttl=timedelta(hours=1), **{**FINGERPRINT, "path": "/y"}
        )
        assert claimed
        assert record is not None
        assert record.path == "/y"
        assert IdempotencyRecord.objects.expired().count() == 0


def test_to_string_redacts_the_key() -> None:
    """The admin and logs see only the key's first eight characters."""
    record, _ = IdempotencyRecord.objects.reserve(
        key="abcdefghijklmnop", ttl=timedelta(hours=1), **FINGERPRINT
    )
    assert record is not None
    assert str(record) == "POST /x key=abcdefgh… reserved"
