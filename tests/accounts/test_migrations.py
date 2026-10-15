"""Tests for accounts migration 0003: stored email addresses become their keys."""

from datetime import timedelta
from typing import Any

import pytest
from django.contrib.sessions.backends.db import SessionStore
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from apps.accounts.models import email_key

BEFORE = [("accounts", "0002_reviewer_account")]
AFTER = [("accounts", "0003_keep_email_keys_not_addresses")]


def migrate(targets: list[tuple[str, str]]) -> Any:
    """Migrate to ``targets`` and return the historical apps there."""
    executor = MigrationExecutor(connection)
    executor.migrate(targets)
    executor.loader.build_graph()
    return executor.loader.project_state(targets).apps


@pytest.mark.django_db(transaction=True)
def test_addresses_become_keys_and_are_forgotten() -> None:
    """Every account and sign-in request ends up with a key and no address."""
    old = migrate(BEFORE)
    user_model = old.get_model("auth", "User")
    signed_up = user_model.objects.create(username="walker@example.com", email="walker@example.com")
    staff = user_model.objects.create(username="admin", email="Boss@Example.com", is_staff=True)
    # Two accounts for one address: the one sign-in made keeps the key, the
    # other gets an opaque name.
    duplicate = user_model.objects.create(username="other", email="walker@example.com")
    # Usernames are case-sensitive, so this one exists too and loses the key.
    shouted = user_model.objects.create(username="WALKER@example.com", email="")
    no_address = user_model.objects.create(username="robot", email="")
    # A sign-in abandoned before this change, and an unrelated session.
    old_session = SessionStore()
    old_session.update(
        {"accounts.sign_in_email": "walker@example.com", "accounts.sign_in_request": "abc"}
    )
    old_session.create()
    other_session = SessionStore()
    other_session["cart"] = 1
    other_session.create()
    old.get_model("accounts", "SignInRequest").objects.create(
        email="walker@example.com",
        token_hash="0" * 64,
        code_hash="0" * 64,
        expires_at=timezone.now() + timedelta(minutes=15),
    )

    new = migrate(AFTER)
    users = new.get_model("auth", "User").objects
    assert users.get(pk=signed_up.pk).username == email_key("walker@example.com")
    assert users.get(pk=staff.pk).username == email_key("boss@example.com")
    assert users.get(pk=duplicate.pk).username == f"duplicate-{duplicate.pk}"
    assert users.get(pk=shouted.pk).username == f"duplicate-{shouted.pk}"
    assert not users.filter(username__contains="@").exists()
    assert users.get(pk=no_address.pk).username == "robot"
    assert not users.exclude(email="").exists()
    request = new.get_model("accounts", "SignInRequest").objects.get()
    assert request.email_key == email_key("walker@example.com")
    stored = SessionStore(session_key=old_session.session_key).load()
    assert stored == {"accounts.sign_in_request": "abc"}
    assert SessionStore(session_key=other_session.session_key).load() == {"cart": 1}
