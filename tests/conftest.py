"""Fixtures shared by every test."""

from collections.abc import Iterator
from typing import Any

import pytest
from django.test import Client, override_settings

from tests.factories import UserFactory

IMMEDIATE = {"default": {"BACKEND": "django.tasks.backends.immediate.ImmediateBackend"}}


@pytest.fixture(autouse=True)
def _tasks_run_inline() -> Iterator[None]:
    """Run tasks in the test's own thread, so their email is sent before the response.

    The app runs them on threads (apps.core.task_backends); its own tests
    put that backend back. override_settings rather than pytest-django's
    ``settings`` fixture, because the rail tests have a ``settings`` of
    their own (the rail settings).
    """
    with override_settings(TASKS=IMMEDIATE):
        yield


@pytest.fixture
def user(db: None) -> Any:
    """A signed-up user."""
    return UserFactory.create()


@pytest.fixture
def signed_in(client: Client, user: Any) -> Client:
    """The test client, signed in as ``user``."""
    client.force_login(user)
    return client
