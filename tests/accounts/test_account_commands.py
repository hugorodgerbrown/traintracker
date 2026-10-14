"""Tests for forget_account, block_account and purge_idle_accounts."""

from datetime import timedelta
from io import StringIO
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.utils import timezone
from oauth2_provider.models import AccessToken

from apps.accounts.models import Passkey, SignInRequest, email_key
from apps.accounts.sign_in import user_for
from tests.factories import PasskeyFactory, SignInRequestFactory, UserFactory

pytestmark = pytest.mark.django_db


def run(*args: str) -> str:
    """Run a command and return what it printed."""
    out = StringIO()
    call_command(*args, stdout=out)
    return out.getvalue()


def account(email: str, **fields: Any) -> Any:
    """An account made by signing in for ``email``: its key is the username."""
    return UserFactory.create(address=email, **fields)


def test_forget_is_a_dry_run_by_default() -> None:
    """Without --commit the account stays."""
    account("a@example.com")
    assert "Would delete 1 account" in run("forget_account", "A@Example.com")
    assert get_user_model().objects.count() == 1


def test_forget_deletes_the_account_its_passkeys_and_requests() -> None:
    """--commit leaves nothing about the address, and nothing about anyone else."""
    user = account("a@example.com")
    PasskeyFactory.create(user=user)
    SignInRequestFactory.create(address="a@example.com")
    other = account("b@example.com")
    run("forget_account", "a@example.com", "--commit")
    assert list(get_user_model().objects.all()) == [other]
    assert not Passkey.objects.exists()
    assert not SignInRequest.objects.exists()


def test_forget_refuses_an_unknown_address_and_staff() -> None:
    """Nothing to forget is an error; so is a staff account, which the admin handles."""
    with pytest.raises(CommandError, match="No account"):
        run("forget_account", "nobody@example.com")
    account("staff@example.com", is_staff=True)
    with pytest.raises(CommandError, match="staff"):
        run("forget_account", "staff@example.com", "--commit")


def test_block_closes_an_account() -> None:
    """A blocked account can't sign in again."""
    user = account("a@example.com")
    assert "Would close" in run("block_account", "a@example.com")
    run("block_account", "a@example.com", "--commit")
    user.refresh_from_db()
    assert not user.is_active
    assert user_for(SignInRequestFactory.build(address="a@example.com")) is None


def test_block_stops_an_address_that_has_no_account_yet() -> None:
    """Its first sign-in finds a closed account, not a new open one."""
    assert "Would make a closed account" in run("block_account", "new@example.com")
    run("block_account", "New@Example.com", "--commit")
    assert user_for(SignInRequestFactory.build(address="new@example.com")) is None
    blocked = get_user_model().objects.get()
    assert (blocked.username, blocked.email) == (email_key("new@example.com"), "")


def test_block_refuses_staff() -> None:
    """Staff are changed in the admin."""
    account("staff@example.com", is_staff=True)
    with pytest.raises(CommandError, match="staff"):
        run("block_account", "staff@example.com", "--commit")


def long_ago(days: int = 200) -> Any:
    """A moment over IDLE_DAYS ago."""
    return timezone.now() - timedelta(days=days)


def test_purge_idle_accounts() -> None:
    """Only non-staff accounts with no sign-in and no token for 180 days go."""
    account("idle@example.com", last_login=long_ago(), date_joined=long_ago(300))
    recent = account("recent@example.com", last_login=timezone.now())
    connected = account("connected@example.com", last_login=long_ago(), date_joined=long_ago())
    AccessToken.objects.create(
        user=connected, token="t", expires=timezone.now() + timedelta(hours=1)
    )
    staff = account("staff@example.com", is_staff=True, last_login=long_ago())
    account("never@example.com", last_login=None, date_joined=long_ago())
    assert "2 idle accounts" in run("purge_idle_accounts")
    assert "Deleted 2" in run("purge_idle_accounts", "--commit")
    assert set(get_user_model().objects.all()) == {recent, connected, staff}
