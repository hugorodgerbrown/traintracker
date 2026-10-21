"""A URLconf for the error-page tests: views that fail on purpose, then the real site."""

from django.core.exceptions import PermissionDenied, SuspiciousOperation
from django.http import HttpRequest, HttpResponse
from django.urls import path

from config.urls import handler400, handler403, handler404, handler500, urlpatterns

__all__ = ["handler400", "handler403", "handler404", "handler500", "urlpatterns"]


def forbidden(request: HttpRequest) -> HttpResponse:
    """Raise PermissionDenied, as a view refusing a reader would."""
    raise PermissionDenied


def suspicious(request: HttpRequest) -> HttpResponse:
    """Raise SuspiciousOperation, which Django answers with a 400."""
    raise SuspiciousOperation("test")


def broken(request: HttpRequest) -> HttpResponse:
    """Raise an unhandled exception: a 500."""
    raise RuntimeError("test")


urlpatterns = [
    path("raise/403/", forbidden),
    path("raise/400/", suspicious),
    path("raise/500/", broken),
    path("mcp/raise/500/", broken),
    *urlpatterns,
]
