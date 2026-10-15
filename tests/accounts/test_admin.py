"""Tests for the accounts admin: users listed by masked address, found by address."""

import pytest
from django.test import Client
from django.urls import reverse

from tests.factories import SignInRequestFactory, UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff_client(client: Client) -> Client:
    """A client signed in as a superuser."""
    client.force_login(UserFactory.create(is_staff=True, is_superuser=True))
    return client


def test_users_are_listed_masked_and_found_by_address(staff_client: Client) -> None:
    """The list shows masked addresses; searching a whole address finds its account."""
    UserFactory.create(address="walker@example.com")
    UserFactory.create(address="other@example.com")
    url = reverse("admin:accounts_user_changelist")
    listing = staff_client.get(url).content.decode()
    assert "w•••@example.com" in listing
    found = staff_client.get(url, {"q": "Walker@example.com"}).content.decode()
    assert "w•••@example.com" in found
    assert "o•••@example.com" not in found


def test_a_user_opens_but_cannot_be_added(staff_client: Client) -> None:
    """Accounts are made by signing in; an existing one opens in the admin."""
    user = UserFactory.create(address="walker@example.com")
    assert staff_client.get(reverse("admin:accounts_user_add")).status_code == 403
    page = staff_client.get(reverse("admin:accounts_user_change", args=[user.pk]))
    assert page.status_code == 200
    assert "w•••@example.com" in page.content.decode()


def test_sign_in_requests_show_the_masked_address(staff_client: Client) -> None:
    """A request is listed by its masked address, never the address."""
    SignInRequestFactory.create(address="walker@example.com")
    listing = staff_client.get(reverse("admin:accounts_signinrequest_changelist"))
    assert "w•••@example.com" in listing.content.decode()
