"""Fixtures shared by every test."""

from typing import Any

import pytest
from django.test import Client

from tests.factories import UserFactory


@pytest.fixture
def user(db: None) -> Any:
    """A signed-up user."""
    return UserFactory.create()


@pytest.fixture
def signed_in(client: Client, user: Any) -> Client:
    """The test client, signed in as ``user``."""
    client.force_login(user)
    return client
