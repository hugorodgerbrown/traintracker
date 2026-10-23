"""Tests for apps.core.views."""

from datetime import UTC, datetime, timedelta
from typing import Any
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


@pytest.mark.django_db
def test_security_txt_follows_rfc_9116(client: Client, settings: Any) -> None:
    """security.txt is plain text naming the contact, a future Expires and its own address."""
    settings.SITE_URL = "https://example.ultraniche.ai"
    settings.SECURITY_CONTACT = "mailto:sec@ultraniche.ai"
    with CaptureQueriesContext(connection) as queries:
        response = client.get("/.well-known/security.txt")
    assert response.status_code == 200
    assert response["Content-Type"] == "text/plain; charset=utf-8"
    assert "max-age=86400" in response["Cache-Control"]
    assert len(queries) == 0
    fields = dict(line.split(": ", 1) for line in response.content.decode().splitlines())
    assert fields["Contact"] == "mailto:sec@ultraniche.ai"
    assert fields["Preferred-Languages"] == "en"
    assert fields["Canonical"] == "https://example.ultraniche.ai/.well-known/security.txt"
    expires = datetime.fromisoformat(fields["Expires"])
    assert fields["Expires"].endswith("Z")
    now = datetime.now(UTC)
    assert now + timedelta(days=179) < expires < now + timedelta(days=365)
