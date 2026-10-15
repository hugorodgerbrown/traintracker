"""Tests for the directory reviewers' account: a fixed code, and no email."""

from typing import Any

import pytest
from django.core import mail
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from apps.accounts import sign_in

pytestmark = pytest.mark.django_db


@pytest.fixture
def reviewer(settings: Any) -> str:
    """Turn the reviewer account on; return its address."""
    settings.REVIEWER_EMAIL = "Reviewer@Example.com"
    settings.REVIEWER_CODE = "482913"
    return "reviewer@example.com"


def test_reviewer_gets_the_fixed_code(reviewer: str) -> None:
    """The reviewers' address, however it is typed, is issued REVIEWER_CODE."""
    request, _, code = sign_in.issue(" REVIEWER@example.com ")
    assert code == "482913"
    assert sign_in.redeem_code(str(request.uuid), "482913") == request


def test_reviewer_code_is_only_for_the_reviewer(reviewer: str) -> None:
    """Anyone else still gets a random code."""
    assert sign_in.reviewer_code("walker@example.com") is None
    assert sign_in.reviewer_code("reviewer@example.com.evil") is None


@pytest.mark.parametrize(("email", "code"), [("", "482913"), ("reviewer@example.com", "")])
def test_reviewer_account_is_off_unless_both_are_set(settings: Any, email: str, code: str) -> None:
    """Without both settings there is no fixed code."""
    settings.REVIEWER_EMAIL = email
    settings.REVIEWER_CODE = code
    assert sign_in.reviewer_code("reviewer@example.com") is None


def test_a_malformed_reviewer_code_turns_it_off(
    reviewer: str, settings: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """A code that isn't six digits is refused, and says why in the log."""
    settings.REVIEWER_CODE = "1234"
    assert sign_in.reviewer_code(reviewer) is None
    assert "REVIEWER_CODE must be 6 digits" in caplog.text


def test_wrong_reviewer_codes_still_run_out(reviewer: str, settings: Any) -> None:
    """The fixed code gets the same five tries per request as any other."""
    request, _, _ = sign_in.issue(reviewer)
    for _ in range(settings.SIGN_IN_MAX_CODE_ATTEMPTS):
        assert sign_in.redeem_code(str(request.uuid), "000000") is None
    assert sign_in.redeem_code(str(request.uuid), "482913") is None


def test_reviewer_signs_in_with_the_fixed_code_and_no_email(client: Client, settings: Any) -> None:
    """The reviewers' address is sent nothing, and its fixed code signs in."""
    cache.clear()
    settings.REVIEWER_EMAIL = "reviewer@example.com"
    settings.REVIEWER_CODE = "482913"
    assert (
        client.post(reverse("accounts:sign_in"), {"email": "reviewer@example.com"}).status_code
        == 302
    )
    assert len(mail.outbox) == 0
    response = client.post(reverse("accounts:sign_in_code"), {"code": "482913"})
    assert response.status_code == 302
    assert client.session.get("_auth_user_id")
