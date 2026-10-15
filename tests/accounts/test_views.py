"""Tests for apps.accounts.views: signing in by email, out, and the account page."""

import re
from typing import Any
from urllib.parse import urlsplit

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.core.mail import EmailMultiAlternatives
from django.test import Client
from django.urls import reverse

from apps.accounts.models import SignInRequest, email_key
from tests.factories import PasskeyFactory, UserFactory

pytestmark = pytest.mark.django_db

SIGN_IN = reverse("accounts:sign_in")
CODE = reverse("accounts:sign_in_code")


@pytest.fixture(autouse=True)
def _fresh_rate_limits() -> None:
    """Rate-limit counters live in the cache; start each test with none."""
    cache.clear()


def request_email(client: Client, email: str = "walker@example.com", next_url: str = "") -> Any:
    """Ask for a sign-in email; return the response."""
    return client.post(SIGN_IN, {"email": email, "next": next_url})


def emailed() -> tuple[str, str]:
    """Return the path of the last email's link, and its code."""
    body = str(mail.outbox[-1].body)
    link = re.search(r"https?://\S+/signin/link/\S+/", body)
    code = re.search(r"^(\d{6})$", body, re.MULTILINE)
    assert link, body
    assert code, body
    return urlsplit(link.group()).path, code.group(1)


def signed_in_user(client: Client) -> Any:
    """Return the user the client's session belongs to, or None."""
    user_id = client.session.get("_auth_user_id")
    return get_user_model().objects.filter(pk=user_id).first() if user_id else None


# ---------- asking for an email ----------


def test_sign_in_page_offers_email_and_passkey(client: Client) -> None:
    """The page has the email field and the hidden passkey card, which asks only when pressed."""
    response = client.get(SIGN_IN, {"next": "/app/account/"})
    content = response.content.decode()
    assert response.status_code == 200
    assert 'autocomplete="email"' in content
    assert "data-passkey-sign-in" in content
    assert 'value="/app/account/"' in content


def test_signed_in_visitors_skip_the_form(signed_in: Client) -> None:
    """Someone already signed in goes straight on."""
    response = signed_in.get(SIGN_IN, {"next": "/app/account/"})
    assert response["Location"] == "/app/account/"


def test_sign_in_emails_a_link_and_a_code(client: Client) -> None:
    """One email, with a link to this site and a six-digit code, then the code page."""
    response = request_email(client, "Walker@Example.com")
    assert response["Location"] == CODE
    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.to == ["walker@example.com"]
    link, code = emailed()
    assert code in message.subject
    assert link.startswith("/signin/link/")
    page = client.get(CODE).content.decode()
    assert "w•••@example.com" in page
    assert "walker@example.com" not in page
    assert "walker@example.com" not in str(dict(client.session))


def test_sign_in_email_has_a_simple_html_version(client: Client) -> None:
    """The HTML half carries the same code and link as the text half, escaped."""
    request_email(client)
    link, code = emailed()
    message = mail.outbox[0]
    assert isinstance(message, EmailMultiAlternatives)
    content, kind = message.alternatives[0]
    assert kind == "text/html"
    html = str(content)
    assert code in html
    assert link in html


def test_code_page_draws_the_code_as_six_boxes(client: Client) -> None:
    """The code field carries code_input.js's hook, and the page loads the script."""
    request_email(client)
    page = client.get(CODE).content.decode()
    assert re.search(r'<input id="code"[^>]*data-code-input data-code-length="6"', page)
    assert "js/code_input.js" in page


def test_a_bad_email_is_refused(client: Client) -> None:
    """An invalid address re-renders the form with an error, and sends nothing."""
    response = request_email(client, "not an email")
    assert response.status_code == 400
    assert not mail.outbox


def test_sign_in_emails_are_rate_limited(client: Client) -> None:
    """The sixth email to one address within the hour is refused."""
    for _ in range(5):
        assert request_email(client).status_code == 302
    response = request_email(client)
    assert response.status_code == 429
    assert len(mail.outbox) == 5


def test_code_page_needs_a_request_from_this_browser(client: Client) -> None:
    """Without a pending request in the session there is nothing to type a code for."""
    assert client.get(CODE)["Location"] == SIGN_IN


# ---------- the link ----------


def test_link_asks_for_a_click_then_signs_in(client: Client) -> None:
    """GET shows Continue without signing in (scanners); POST signs in and creates the account."""
    request_email(client, next_url="/app/account/")
    link, _ = emailed()
    other_browser = Client()
    page = other_browser.get(link)
    assert page.status_code == 200
    assert b"walker" not in page.content
    assert signed_in_user(other_browser) is None

    response = other_browser.post(link)
    assert response["Location"] == "/app/account/"
    user = signed_in_user(other_browser)
    assert user is not None
    assert (user.username, user.email) == (email_key("walker@example.com"), "")


def test_a_used_link_has_expired(client: Client) -> None:
    """The second use of a link shows the expired page."""
    request_email(client)
    link, _ = emailed()
    client.post(link)
    assert Client().get(link).status_code == 410
    assert Client().post(link).status_code == 410


def test_a_link_for_a_closed_account_does_not_sign_in(client: Client) -> None:
    """An inactive account gets the closed page."""
    UserFactory.create(address="walker@example.com", is_active=False)
    request_email(client)
    link, _ = emailed()
    response = client.post(link)
    assert response.status_code == 403
    assert signed_in_user(client) is None


def test_next_from_elsewhere_is_ignored(client: Client) -> None:
    """A next pointing off-site lands on the app instead."""
    request_email(client, next_url="https://evil.example/")
    link, _ = emailed()
    assert client.post(link)["Location"] == reverse("accounts:account")


def test_a_first_sign_in_is_marked_as_a_sign_up(client: Client, settings: Any) -> None:
    """With fivebar on, a new account's redirect carries #signed-up; the next sign-in doesn't."""
    settings.FIVEBAR_DOMAIN = "example.ultraniche.ai"
    request_email(client)
    _, code = emailed()
    assert (
        client.post(CODE, {"code": code})["Location"] == reverse("accounts:account") + "#signed-up"
    )

    again = Client()
    request_email(again)
    _, code = emailed()
    assert again.post(CODE, {"code": code})["Location"] == reverse("accounts:account")


def test_no_sign_up_mark_while_fivebar_is_off(client: Client) -> None:
    """Off, a new account's redirect is unchanged."""
    request_email(client)
    link, _ = emailed()
    assert client.post(link)["Location"] == reverse("accounts:account")


# ---------- the code ----------


def test_code_signs_in_this_browser(client: Client) -> None:
    """The code from the email, typed where it was asked for, signs in."""
    request_email(client)
    _, code = emailed()
    response = client.post(CODE, {"code": code})
    assert response["Location"] == reverse("accounts:account")
    assert signed_in_user(client) is not None
    assert SignInRequest.objects.get().used_at is not None


def test_code_does_not_work_in_another_browser(client: Client) -> None:
    """Another browser has no pending request, so it can't use the code."""
    request_email(client)
    _, code = emailed()
    other = Client()
    assert other.post(CODE, {"code": code})["Location"] == SIGN_IN
    assert signed_in_user(other) is None


def test_a_wrong_code_says_so(client: Client) -> None:
    """A wrong or malformed code re-renders the page with an error."""
    request_email(client)
    _, code = emailed()
    wrong = "000000" if code != "000000" else "111111"
    response = client.post(CODE, {"code": wrong})
    assert response.status_code == 400
    assert b"didn&#x27;t work" in response.content
    assert client.post(CODE, {"code": "12"}).status_code == 400
    assert signed_in_user(client) is None


def test_code_guesses_are_rate_limited(client: Client) -> None:
    """Past the limit, even the right code waits."""
    request_email(client)
    _, code = emailed()
    for _ in range(20):
        client.post(CODE, {"code": "12"})
    assert client.post(CODE, {"code": code}).status_code == 429


# ---------- signing out, and the admin ----------


def test_sign_out_clears_the_cache_and_goes_home(signed_in: Client) -> None:
    """Signing out ends the session and tells the browser to drop cached pages."""
    response = signed_in.post(reverse("accounts:sign_out"))
    assert response["Location"] == reverse("accounts:sign_in")
    assert response["Clear-Site-Data"] == '"cache"'
    assert signed_in_user(signed_in) is None


def test_admin_sign_in_is_the_normal_sign_in(client: Client) -> None:
    """The admin's login page sends staff to the email sign-in, keeping where they were going."""
    response = client.get("/admin/login/?next=/admin/auth/")
    assert response["Location"] == f"{SIGN_IN}?next=%2Fadmin%2Fauth%2F"


def test_admin_refuses_signed_in_non_staff(signed_in: Client) -> None:
    """A signed-in user who isn't staff gets a 403, not a loop."""
    assert signed_in.get("/admin/login/").status_code == 403


# ---------- the account page ----------


def test_account_page_lists_my_passkeys(signed_in: Client, user: Any) -> None:
    """The account page shows my passkeys, nobody else's, and says no address is kept."""
    PasskeyFactory.create(user=user, name="My phone")
    PasskeyFactory.create(name="Someone else's")
    content = signed_in.get(reverse("accounts:account")).content.decode()
    assert "email address isn" in content
    assert "My phone" in content
    assert "Someone else" not in content
    assert "data-sign-out" in content


def test_account_page_needs_sign_in(client: Client) -> None:
    """Signed-out visitors go to sign in, and come back."""
    response = client.get(reverse("accounts:account"))
    assert response["Location"].startswith(f"{SIGN_IN}?next=")


def test_delete_my_passkey(signed_in: Client, user: Any) -> None:
    """I can remove my own passkey."""
    passkey = PasskeyFactory.create(user=user)
    response = signed_in.post(reverse("accounts:passkey_delete", args=[passkey.uuid]))
    assert response["Location"] == reverse("accounts:account")
    assert not user.passkeys.exists()


def test_cannot_delete_someone_elses_passkey(signed_in: Client) -> None:
    """Another user's passkey is a 404."""
    passkey = PasskeyFactory.create()
    response = signed_in.post(reverse("accounts:passkey_delete", args=[passkey.uuid]))
    assert response.status_code == 404
