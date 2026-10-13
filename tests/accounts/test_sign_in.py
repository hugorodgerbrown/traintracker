"""Tests for apps.accounts.sign_in: issuing and redeeming links and codes."""

from datetime import timedelta
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.accounts import sign_in
from apps.accounts.models import SignInRequest
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_issue_stores_hashes_only() -> None:
    """Neither the token nor the code is stored as given."""
    request, token, code = sign_in.issue("  Walker@Example.COM ", "/app/")
    assert request.email == "walker@example.com"
    assert len(code) == sign_in.CODE_DIGITS
    assert code.isdigit()
    assert token not in request.token_hash
    assert code not in request.code_hash
    assert request.next_url == "/app/"


def test_a_new_request_ends_the_previous_one() -> None:
    """Only the newest email for an address works."""
    first, first_token, _ = sign_in.issue("a@example.com")
    sign_in.issue("a@example.com")
    assert sign_in.redeem_token(first_token) is None
    first.refresh_from_db()
    assert first.used_at is not None


def test_token_works_once() -> None:
    """A link signs in once; the second click finds nothing."""
    request, token, _ = sign_in.issue("a@example.com")
    assert sign_in.find_by_token(token) == request
    assert sign_in.redeem_token(token) == request
    assert sign_in.redeem_token(token) is None
    assert sign_in.find_by_token(token) is None


def test_token_expires() -> None:
    """A link past its time doesn't work."""
    request, token, _ = sign_in.issue("a@example.com")
    SignInRequest.objects.filter(pk=request.pk).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    assert sign_in.redeem_token(token) is None


def test_unknown_token() -> None:
    """A made-up link finds nothing."""
    assert sign_in.redeem_token("nope") is None


def test_code_works_once() -> None:
    """The right code, with spaces, redeems the request once."""
    request, _, code = sign_in.issue("a@example.com")
    assert sign_in.redeem_code(str(request.uuid), f" {code} ") == request
    assert sign_in.redeem_code(str(request.uuid), code) is None


def test_wrong_codes_run_out(settings: Any) -> None:
    """After the allowed wrong guesses, even the right code is refused."""
    request, _, code = sign_in.issue("a@example.com")
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(settings.SIGN_IN_MAX_CODE_ATTEMPTS):
        assert sign_in.redeem_code(str(request.uuid), wrong) is None
    request.refresh_from_db()
    assert request.code_attempts == settings.SIGN_IN_MAX_CODE_ATTEMPTS
    assert sign_in.redeem_code(str(request.uuid), code) is None


def test_code_for_another_request_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """A code only fits the request it was made for."""
    codes = iter([111111, 222222])
    monkeypatch.setattr(sign_in.secrets, "randbelow", lambda _: next(codes))
    _, _, first_code = sign_in.issue("a@example.com")
    second, _, second_code = sign_in.issue("b@example.com")
    assert (first_code, second_code) == ("111111", "222222")
    assert sign_in.redeem_code(str(second.uuid), first_code) is None


def test_first_sign_in_creates_the_account() -> None:
    """There is no sign-up step: the first redeemed email makes the account."""
    request, token, _ = sign_in.issue("new@example.com")
    user = sign_in.user_for(sign_in.redeem_token(token) or request)
    assert user is not None
    assert user.username == user.email == "new@example.com"
    assert not user.has_usable_password()


def test_existing_accounts_are_found_by_username_or_email() -> None:
    """A createsuperuser account with a different username is found by its email."""
    admin = get_user_model().objects.create_superuser("admin", "Boss@example.com")
    request, _, _ = sign_in.issue("boss@example.com")
    assert sign_in.user_for(request) == admin
    member = UserFactory.create(email="m@example.com")
    request, _, _ = sign_in.issue("m@example.com")
    assert sign_in.user_for(request) == member


def test_a_closed_account_cannot_sign_in() -> None:
    """An inactive account stays out."""
    UserFactory.create(email="gone@example.com", is_active=False)
    request, _, _ = sign_in.issue("gone@example.com")
    assert sign_in.user_for(request) is None
