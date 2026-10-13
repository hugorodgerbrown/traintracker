"""Tests for apps.accounts.models."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts.models import Passkey, SignInRequest
from tests.factories import PasskeyFactory, SignInRequestFactory, UserFactory

pytestmark = pytest.mark.django_db


def test_sign_in_request_querysets() -> None:
    """live() is unused and unexpired; expired() is anything past its time."""
    live = SignInRequestFactory.create(email="A@example.com")
    used = SignInRequestFactory.create(used_at=timezone.now())
    old = SignInRequestFactory.create(expires_at=timezone.now() - timedelta(minutes=1))
    assert list(SignInRequest.objects.live()) == [live]
    assert list(SignInRequest.objects.expired()) == [old]
    assert list(SignInRequest.objects.for_email(" a@example.com")) == []
    assert used not in SignInRequest.objects.live()


def test_sign_in_request_to_string() -> None:
    """The string names the address and the state."""
    request = SignInRequestFactory.create(email="a@example.com")
    assert str(request) == "Sign-in for a@example.com (unused)"
    request.used_at = timezone.now()
    assert request.to_string() == "Sign-in for a@example.com (used)"


def test_passkeys_for_user() -> None:
    """for_user() returns that user's passkeys only."""
    user = UserFactory.create()
    mine = PasskeyFactory.create(user=user)
    PasskeyFactory.create()
    assert list(Passkey.objects.for_user(user)) == [mine]
    assert str(mine) == "Passkey on iPhone"
