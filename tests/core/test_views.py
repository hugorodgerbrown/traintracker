"""Tests for apps.core.views."""

from unittest import mock

import pytest
from django.db import OperationalError, connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse


@pytest.mark.django_db
def test_livez_needs_no_sign_in_or_database(client: Client) -> None:
    """Render's health check answers 200 to anyone, without a query."""
    with CaptureQueriesContext(connection) as queries:
        response = client.get(reverse("livez"))
    assert response.status_code == 200
    assert response.content == b"ok"
    assert len(queries) == 0
    assert "no-cache" in response["Cache-Control"]


@pytest.mark.django_db
def test_healthz_reads_the_database(client: Client) -> None:
    """/healthz answers 200 once the database answers a read."""
    response = client.get(reverse("healthz"))
    assert response.status_code == 200
    assert response.content == b"ok"


@pytest.mark.django_db
def test_healthz_is_503_when_the_database_fails(client: Client) -> None:
    """A failed read is a 503 with a fixed body: no driver message leaks."""
    with mock.patch("apps.core.views.get_user_model") as get_user_model:
        exists = get_user_model.return_value.objects.exists
        exists.side_effect = OperationalError("host=db.internal")
        response = client.get(reverse("healthz"))
    assert response.status_code == 503
    assert response.content == b"error"


@pytest.mark.urls("tests.core.urls")
def test_csrf_failure_is_marked_for_the_outbox(user: object) -> None:
    """A CSRF rejection carries X-CSRF-Failure, so the outbox waits instead of dropping."""
    client = Client(enforce_csrf_checks=True)
    client.force_login(user)  # type: ignore[arg-type]
    response = client.post("/write/", "{}", content_type="application/json")
    assert response.status_code == 403
    assert response["X-CSRF-Failure"] == "1"
