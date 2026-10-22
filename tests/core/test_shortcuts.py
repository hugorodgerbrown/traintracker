"""Tests for apps.core.shortcuts: render tells the template its status."""

from unittest import mock

from django.test import RequestFactory

from apps.core import shortcuts


def test_render_puts_the_status_in_the_context() -> None:
    """The template sees the same status the response is sent with."""
    request = RequestFactory().get("/")
    with mock.patch.object(shortcuts, "django_render") as django_render:
        shortcuts.render(request, "page.html", {"a": 1}, status=410)
    django_render.assert_called_once_with(
        request, "page.html", {"a": 1, "http_status": 410}, status=410
    )


def test_render_defaults_to_200_without_a_context() -> None:
    """No context and no status is an ordinary page."""
    request = RequestFactory().get("/")
    with mock.patch.object(shortcuts, "django_render") as django_render:
        shortcuts.render(request, "page.html")
    django_render.assert_called_once_with(request, "page.html", {"http_status": 200}, status=200)
