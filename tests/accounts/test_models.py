"""Tests for apps.accounts.models."""

from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone

from apps.accounts.models import Passkey, SignInRequest, email_key
from tests.factories import PasskeyFactory, SignInRequestFactory, UserFactory

pytestmark = pytest.mark.django_db


def test_sign_in_request_querysets() -> None:
    """live() is unused and unexpired; expired() is anything past its time."""
    live = SignInRequestFactory.create(address="A@example.com")
    used = SignInRequestFactory.create(used_at=timezone.now())
    old = SignInRequestFactory.create(expires_at=timezone.now() - timedelta(minutes=1))
    assert list(SignInRequest.objects.live()) == [live]
    assert list(SignInRequest.objects.expired()) == [old]
    assert list(SignInRequest.objects.for_email(" a@Example.com")) == [live]
    assert not SignInRequest.objects.for_email("b@example.com").exists()
    assert used not in SignInRequest.objects.live()


def test_sign_in_request_to_string() -> None:
    """The string names the request and its state, never the address."""
    request = SignInRequestFactory.create(address="a@example.com")
    assert str(request) == f"Sign-in request {request.uuid} (unused)"
    request.used_at = timezone.now()
    assert request.to_string() == f"Sign-in request {request.uuid} (used)"
    assert "a@example.com" not in request.to_string()


def test_email_key_is_stable_case_blind_and_not_the_address() -> None:
    """One address always gives one key, whatever its case or spaces; it isn't the address."""
    key = email_key("Walker@Example.com ")
    assert key == email_key("walker@example.com")
    assert key != email_key("walker2@example.com")
    assert len(key) == 64
    assert "walker" not in key


def test_email_key_depends_on_the_secret_key(settings: Any) -> None:
    """Without the secret key, a guessed address can't be checked against a key."""
    key = email_key("walker@example.com")
    settings.SECRET_KEY = "another-secret-key-" + "x" * 40
    assert email_key("walker@example.com") != key


def test_passkeys_for_user() -> None:
    """for_user() returns that user's passkeys only."""
    user = UserFactory.create()
    mine = PasskeyFactory.create(user=user)
    PasskeyFactory.create()
    assert list(Passkey.objects.for_user(user)) == [mine]
    assert str(mine) == "Passkey on iPhone"
