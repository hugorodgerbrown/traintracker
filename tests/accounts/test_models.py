"""Tests for apps.accounts.models."""

from datetime import timedelta
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone

from apps.accounts.checks import email_key_is_set
from apps.accounts.models import Passkey, SignInRequest, User, email_key, mask_email
from tests.factories import PasskeyFactory, SignInRequestFactory, UserFactory

pytestmark = pytest.mark.django_db


def test_sign_in_request_querysets() -> None:
    """live() is unused and unexpired; expired() is anything past its time."""
    live = SignInRequestFactory.create(address="A@example.com")
    used = SignInRequestFactory.create(used_at=timezone.now())
    old = SignInRequestFactory.create(expires_at=timezone.now() - timedelta(minutes=1))
    assert list(SignInRequest.objects.live()) == [live]
    assert list(SignInRequest.objects.expired()) == [old]
    assert list(SignInRequest.objects.for_email(" a@example.com")) == [live]
    assert list(SignInRequest.objects.for_email("b@example.com")) == []
    assert used not in SignInRequest.objects.live()


def test_sign_in_request_to_string() -> None:
    """The string names the masked address and the state."""
    request = SignInRequestFactory.create(address="walker@example.com")
    assert str(request) == "Sign-in for w•••@example.com (unused)"
    request.used_at = timezone.now()
    assert request.to_string() == "Sign-in for w•••@example.com (used)"


def test_passkeys_for_user() -> None:
    """for_user() returns that user's passkeys only."""
    user = UserFactory.create()
    mine = PasskeyFactory.create(user=user)
    PasskeyFactory.create()
    assert list(Passkey.objects.for_user(user)) == [mine]
    assert str(mine) == "Passkey on iPhone"


def test_email_key_is_its_own_secret(settings: Any) -> None:
    """The key ignores case and spaces, and SECRET_KEY; ACCOUNTS_EMAIL_KEY changes it."""
    key = email_key("walker@example.com")
    assert len(key) == 64
    assert email_key(" Walker@Example.com ") == key
    settings.SECRET_KEY = "a-different-secret-key-0123456789-abcdefghijklmnopqrstuvwxyz"
    assert email_key("walker@example.com") == key
    settings.ACCOUNTS_EMAIL_KEY = "another-email-key"
    assert email_key("walker@example.com") != key


def test_email_key_needs_its_secret(settings: Any) -> None:
    """With no ACCOUNTS_EMAIL_KEY nothing is keyed, and the checks fail."""
    settings.ACCOUNTS_EMAIL_KEY = ""
    with pytest.raises(ImproperlyConfigured):
        email_key("walker@example.com")
    assert [e.id for e in email_key_is_set(None)] == ["accounts.E001"]


def test_email_key_check_passes_when_set() -> None:
    """The check is quiet when the key is set."""
    assert email_key_is_set(None) == []


def test_mask_email() -> None:
    """The first letter and the domain, normalised."""
    assert mask_email(" Hugo@Rodger-Brown.com") == "h•••@rodger-brown.com"


def test_users_by_email_and_staff() -> None:
    """for_email() finds an account by its address; staff() finds staff."""
    member = UserFactory.create(address="m@example.com")
    boss = UserFactory.create(is_staff=True)
    assert list(User.objects.for_email("M@example.com")) == [member]
    assert list(User.objects.staff()) == [boss]


def test_user_to_string() -> None:
    """An account reads as its masked address, or its username without one."""
    assert str(UserFactory.create(address="walker@example.com")) == "w•••@example.com"
    assert str(UserFactory.create(username="admin", masked_email="")) == "admin"
