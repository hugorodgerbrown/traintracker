"""Tests for reviewer accounts: a fixed code, no email, managed in the admin."""

from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from apps.accounts import models, sign_in
from apps.accounts.models import REVIEWER_CODE_DIGITS, ReviewerAccount
from tests.factories import ReviewerAccountFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _fresh_rate_limits() -> None:
    """Rate-limit counters live in the cache; start each test with none."""
    cache.clear()


def test_a_code_is_made_on_save() -> None:
    """A new account gets six random digits, and its address is lower-cased."""
    account = ReviewerAccount.objects.create(email=" Reviewer@Example.com ")
    assert account.email == "reviewer@example.com"
    assert len(account.code) == sign_in.CODE_DIGITS
    assert account.code.isdigit()
    assert str(account) == "reviewer@example.com"


def test_saving_again_keeps_the_code() -> None:
    """Editing the address doesn't change the code the reviewers were given."""
    account = ReviewerAccountFactory.create()
    code = account.code
    account.email = "other@example.com"
    account.save()
    account.refresh_from_db()
    assert account.code == code


def test_reviewer_gets_the_fixed_code() -> None:
    """The reviewer's address, however it is typed, is issued its code."""
    account = ReviewerAccountFactory.create(email="reviewer@example.com")
    request, _, code = sign_in.issue(" REVIEWER@example.com ")
    assert code == account.code
    assert sign_in.redeem_code(str(request.uuid), account.code) == request


def test_anyone_else_gets_a_random_code() -> None:
    """Only a reviewer account's address has a fixed code."""
    ReviewerAccountFactory.create(email="reviewer@example.com")
    assert sign_in.reviewer_code("walker@example.com") is None


def test_wrong_reviewer_codes_still_run_out(settings: Any) -> None:
    """The fixed code gets the same tries per request as any other."""
    account = ReviewerAccountFactory.create(code="482913")
    request, _, _ = sign_in.issue(account.email)
    for _ in range(settings.SIGN_IN_MAX_CODE_ATTEMPTS):
        assert sign_in.redeem_code(str(request.uuid), "000000") is None
    assert sign_in.redeem_code(str(request.uuid), "482913") is None


def test_reviewer_signs_in_with_the_code_and_no_email(client: Client) -> None:
    """The reviewer's address is sent nothing, and its code signs in on the code page."""
    account = ReviewerAccountFactory.create()
    response = client.post(reverse("accounts:sign_in"), {"email": account.email})
    assert response.status_code == 302
    assert len(mail.outbox) == 0
    client.post(reverse("accounts:sign_in_code"), {"code": account.code})
    assert client.session.get("_auth_user_id")


def test_admin_lists_codes_and_gives_new_ones(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Staff read the code in the admin, and the action replaces it."""
    staff = get_user_model().objects.create_superuser("boss@example.com", "boss@example.com")
    client.force_login(staff)
    account = ReviewerAccountFactory.create()
    old = account.code
    monkeypatch.setattr(models, "new_reviewer_code", lambda: "111111")
    listing = client.get(reverse("admin:accounts_revieweraccount_changelist"))
    assert old in listing.content.decode()
    client.post(
        reverse("admin:accounts_revieweraccount_changelist"),
        {"action": "give_new_code", "_selected_action": [account.pk]},
    )
    account.refresh_from_db()
    assert account.code == "111111"


def test_code_lengths_match() -> None:
    """A reviewer code fits the code form."""
    assert REVIEWER_CODE_DIGITS == sign_in.CODE_DIGITS


def test_a_new_code_ends_sign_ins_started_with_the_old_one() -> None:
    """A browser that asked before the code changed can't use the old code."""
    account = ReviewerAccountFactory.create()
    old = account.code
    request, _, _ = sign_in.issue(account.email)
    account.give_new_code()
    assert sign_in.redeem_code(str(request.uuid), old) is None


def test_deleting_ends_sign_ins_it_started(client: Client) -> None:
    """Deleting the account, one or many in the admin, ends its pending sign-ins."""
    staff = get_user_model().objects.create_superuser("boss@example.com", "boss@example.com")
    client.force_login(staff)
    one, two = ReviewerAccountFactory.create(), ReviewerAccountFactory.create()
    requests = [(sign_in.issue(a.email)[0], a.code) for a in (one, two)]
    one.delete()
    client.post(
        reverse("admin:accounts_revieweraccount_changelist"),
        {"action": "delete_selected", "_selected_action": [two.pk], "post": "yes"},
    )
    assert not ReviewerAccount.objects.exists()
    for request, code in requests:
        assert sign_in.redeem_code(str(request.uuid), code) is None


def test_changing_the_address_ends_the_old_ones_sign_ins() -> None:
    """The old address stops being a reviewer address at once, pending sign-ins included."""
    account = ReviewerAccountFactory.create(email="old@example.com")
    request, _, _ = sign_in.issue("old@example.com")
    account.email = "new@example.com"
    account.save()
    assert sign_in.redeem_code(str(request.uuid), account.code) is None


def test_a_save_that_leaves_the_address_alone_ends_nothing() -> None:
    """Only a saved change of address ends sign-ins, not one left out of update_fields."""
    account = ReviewerAccountFactory.create(email="old@example.com")
    request, _, _ = sign_in.issue("old@example.com")
    account.email = "new@example.com"
    account.save(update_fields=["updated_at"])
    assert sign_in.redeem_code(str(request.uuid), account.code) == request
