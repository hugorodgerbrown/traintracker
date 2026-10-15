"""Tests for apps/core/private_pages.py: signed-in pages are never cached."""

from __future__ import annotations

from typing import Any

from django.contrib.auth.models import AnonymousUser
from django.http import HttpRequest, HttpResponse
from django.test import RequestFactory

from apps.core.private_pages import PrivatePagesMiddleware
from tests.factories import UserFactory


def _respond(user: Any, cache_control: str | None = None) -> HttpResponse:
    """Run a GET through the middleware as ``user``; the view may set Cache-Control."""

    def view(request: HttpRequest) -> HttpResponse:
        response = HttpResponse("page")
        if cache_control:
            response["Cache-Control"] = cache_control
        return response

    request = RequestFactory().get("/somewhere/")
    request.user = user
    return PrivatePagesMiddleware(view)(request)


def test_a_signed_in_page_is_not_stored(db: None) -> None:
    """A signed-in user's page that says nothing about caching gets no-store."""
    response = _respond(UserFactory())
    assert "no-store" in response["Cache-Control"]
    assert "private" in response["Cache-Control"]


def test_a_view_that_chose_its_caching_keeps_it(db: None) -> None:
    """A public page stays public for a signed-in user."""
    response = _respond(UserFactory(), "public, max-age=300")
    assert response["Cache-Control"] == "public, max-age=300"


def test_a_signed_out_page_is_left_alone() -> None:
    """Visitors' pages keep the browser's default caching."""
    assert not _respond(AnonymousUser()).has_header("Cache-Control")
