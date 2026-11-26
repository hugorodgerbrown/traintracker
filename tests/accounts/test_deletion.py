"""Tests for deleting an account: apps.accounts.deletion and its views."""

import datetime as dt
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts import deletion
from apps.accounts.deletion import delete_account
from apps.accounts.models import SignInRequest, email_key
from apps.core.models import IdempotencyRecord
from tests.factories import PasskeyFactory, SignInRequestFactory, UserFactory

pytestmark = pytest.mark.django_db

DELETE = reverse("accounts:account_delete")
DELETED = reverse("accounts:account_deleted")
calls: list[Any] = []


def record(user: Any) -> None:
    """A delete hook that notes who it was called for."""
    calls.append(user.pk)


def fail(user: Any) -> None:
    """A delete hook that fails."""
    raise RuntimeError("hook failed")


def test_account_page_offers_deletion(signed_in: Client) -> None:
    """The account page links to the confirmation step."""
    response = signed_in.get(reverse("accounts:account"))
    assert DELETE in response.content.decode()


def test_confirmation_asks_first_and_deletes_nothing(signed_in: Client, user: Any) -> None:
    """The GET is the confirmation: it says it's final and leaves the account."""
    response = signed_in.get(DELETE)
    body = response.content.decode()
    assert response.status_code == 200
    assert "There are no backups" in body
    assert "data-sign-out" in body
    assert get_user_model().objects.filter(pk=user.pk).exists()


def test_confirmation_needs_a_signed_in_account(client: Client) -> None:
    """Signed out, the confirmation sends you to sign in."""
    response = client.get(DELETE)
    assert response.status_code == 302
    assert reverse("accounts:sign_in") in response["Location"]


def test_signed_out_post_deletes_nothing(client: Client) -> None:
    """A POST without a session is sent to sign in, and nothing goes."""
    user = UserFactory.create()
    client.post(DELETE)
    assert get_user_model().objects.filter(pk=user.pk).exists()


def test_deleting_removes_the_account_and_signs_out(signed_in: Client, user: Any) -> None:
    """The POST deletes the account, its passkeys and sign-ins, and lands on the deleted page."""
    PasskeyFactory.create(user=user)
    response = signed_in.post(DELETE)
    assert response["Location"] == DELETED
    assert not get_user_model().objects.filter(pk=user.pk).exists()
    assert "_auth_user_id" not in signed_in.session
    page = signed_in.get(DELETED)
    assert page.status_code == 200
    assert "Your account has been deleted" in page.content.decode()


def test_deletion_leaves_other_accounts(signed_in: Client, user: Any) -> None:
    """Only the signed-in account goes."""
    other = UserFactory.create()
    passkey = PasskeyFactory.create(user=other)
    signed_in.post(DELETE)
    assert get_user_model().objects.filter(pk=other.pk).exists()
    assert passkey.__class__.objects.filter(pk=passkey.pk).exists()


def test_sign_in_requests_for_the_address_go() -> None:
    """Requests keyed by the account's address are deleted with it; others stay."""
    user = UserFactory.create(address="gone@example.com")
    mine = SignInRequestFactory.create(address="gone@example.com")
    theirs = SignInRequestFactory.create(address="stays@example.com")
    delete_account(user)
    assert not SignInRequest.objects.filter(pk=mine.pk).exists()
    assert SignInRequest.objects.filter(pk=theirs.pk).exists()


def test_sign_in_key_when_addresses_are_kept(settings: Any) -> None:
    """With ACCOUNTS_KEEP_EMAIL the username is the address, so its key is worked out."""
    settings.ACCOUNTS_KEEP_EMAIL = True
    user = get_user_model()(username="kept@example.com")
    assert deletion.sign_in_key(user) == email_key("kept@example.com")


def test_hooks_run_before_the_account_goes(settings: Any) -> None:
    """Each hook in ACCOUNTS_DELETE_HOOKS is called with the user."""
    settings.ACCOUNTS_DELETE_HOOKS = ["tests.accounts.test_deletion.record"]
    user = UserFactory.create()
    pk = user.pk
    calls.clear()
    delete_account(user)
    assert calls == [pk]


def test_a_failing_hook_keeps_the_account(settings: Any) -> None:
    """Deletion is one transaction: if a hook fails, nothing is deleted."""
    settings.ACCOUNTS_DELETE_HOOKS = ["tests.accounts.test_deletion.fail"]
    user = UserFactory.create(address="kept@example.com")
    request = SignInRequestFactory.create(address="kept@example.com")
    with pytest.raises(RuntimeError):
        delete_account(user)
    assert get_user_model().objects.filter(pk=user.pk).exists()
    assert SignInRequest.objects.filter(pk=request.pk).exists()


def test_privacy_notice_says_deletion_is_self_serve_and_final(client: Client) -> None:
    """The notice tells people how to delete, and that there are no backups."""
    body = client.get(reverse("public:privacy")).content.decode()
    assert "Delete my account" in body
    assert "no backups" in body
    assert "ask for your account" not in body


def test_stored_outbox_replies_for_the_account_go() -> None:
    """Idempotency records hold what the account wrote; its own go, others stay."""
    user = UserFactory.create()
    other = UserFactory.create()

    def stored(principal: str, key: str) -> IdempotencyRecord:
        """Store a reply for ``principal``, as the outbox middleware would."""
        return IdempotencyRecord.objects.create(
            key=key,
            method="POST",
            path="/app/notes/",
            principal=principal,
            body_hash="0" * 64,
            response_body=b'{"text": "private"}',
            expires_at=timezone.now() + dt.timedelta(days=1),
        )

    mine = stored(str(user.pk), "mine")
    theirs = stored(str(other.pk), "theirs")
    delete_account(user)
    assert not IdempotencyRecord.objects.filter(pk=mine.pk).exists()
    assert IdempotencyRecord.objects.filter(pk=theirs.pk).exists()


def test_traintrackr_pages_carry_the_site_and_name_what_goes(signed_in: Client) -> None:
    """Traintrackr's own confirm and done pages, in the site's header and footer."""
    page = signed_in.get(DELETE).content.decode()
    assert "Delete your account? · Traintrackr" not in page
    assert "Delete your account · Traintrackr" in page
    assert "your saved boards" in page
    assert "Keep my account" in page
    signed_in.post(DELETE)
    done = signed_in.get(DELETED).content.decode()
    assert "Account deleted · Traintrackr" in done
