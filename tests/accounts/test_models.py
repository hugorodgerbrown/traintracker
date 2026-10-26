"""Tests for apps.accounts.models."""

from datetime import timedelta
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.db import IntegrityError
from django.utils import timezone
from mcp_auth.accounts import account_name as mcp_account_name

from apps.accounts import sign_in
from apps.accounts.checks import email_key_is_set
from apps.accounts.models import (
    Passkey,
    SignInRequest,
    User,
    account_name,
    email_key,
    mask_email,
)
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


def test_the_consent_page_names_the_masked_address() -> None:
    """mcp-auth's consent page names an account by its masked address, never its key."""
    user = UserFactory.create(address="walker@example.com")
    assert account_name(user) == mcp_account_name(user) == "w•••@example.com"


@pytest.mark.parametrize("keep", [False, True])
def test_create_user_stores_the_account_as_sign_in_does(settings: Any, keep: bool) -> None:
    """An account made with an address is keyed and masked; the username typed is ignored."""
    settings.ACCOUNTS_KEEP_EMAIL = keep
    user = User.objects.create_user("anything", " Walker@Example.com")
    twin = UserFactory.build(address="walker@example.com")
    assert (user.username, user.email, user.masked_email) == (
        twin.username,
        twin.email,
        "w•••@example.com",
    )
    assert sign_in.user_for(sign_in.issue("walker@example.com")[0]) == user


def test_create_user_without_an_address_keeps_the_username() -> None:
    """Without an address there is nothing to key, so the username stands."""
    assert User.objects.create_user("robot").username == "robot"


def test_create_user_refuses_a_second_account_for_an_address() -> None:
    """The keyed username is unique, so an address has one account."""
    UserFactory.create(address="walker@example.com")
    with pytest.raises(IntegrityError):
        User.objects.create_user("walker", "walker@example.com")


@pytest.mark.parametrize("keep", [False, True])
def test_createsuperuser_promotes_the_account_sign_in_made(settings: Any, keep: bool) -> None:
    """Running createsuperuser after signing in makes that account staff, not a second one."""
    settings.ACCOUNTS_KEEP_EMAIL = keep
    member = sign_in.user_for(sign_in.issue("boss@example.com")[0])
    assert member is not None
    call_command(
        "createsuperuser",
        username="admin",
        email="Boss@example.com",
        interactive=False,
        verbosity=0,
    )
    assert list(User.objects.all()) == [member]
    member.refresh_from_db()
    assert member.is_staff
    assert member.is_superuser


def test_createsuperuser_before_sign_in_is_the_account_sign_in_finds() -> None:
    """A superuser made first is keyed at once, and signing in reaches it."""
    call_command(
        "createsuperuser",
        username="admin",
        email="boss@example.com",
        interactive=False,
        verbosity=0,
    )
    (admin,) = User.objects.all()
    assert admin.is_superuser
    assert admin.email == ""
    assert sign_in.user_for(sign_in.issue("boss@example.com")[0]) == admin
    assert User.objects.count() == 1
