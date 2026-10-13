"""Tests for apps.core.redirects.safe_next."""

import pytest
from django.test import RequestFactory
from django.urls import reverse

from apps.core.redirects import safe_next


@pytest.mark.parametrize(
    ("candidate", "expected"),
    [
        ("/app/account/", "/app/account/"),
        ("/oauth/authorize/?client_id=x", "/oauth/authorize/?client_id=x"),
        ("https://evil.example/", None),
        ("//evil.example/", None),
        ("/\\evil.example", None),
        ("", None),
        (None, None),
    ],
)
def test_safe_next(rf: RequestFactory, candidate: str | None, expected: str | None) -> None:
    """Only same-site paths survive; anything else is the app's start page."""
    assert safe_next(rf.get("/"), candidate) == (expected or reverse("accounts:account"))
