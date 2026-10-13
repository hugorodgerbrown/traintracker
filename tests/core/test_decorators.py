"""Tests for apps.core.decorators."""

from django.contrib.auth.models import AnonymousUser
from django.http import HttpRequest, HttpResponse
from django.test import RequestFactory

from apps.core.decorators import login_required_json, require_htmx


def ok(request: HttpRequest) -> HttpResponse:
    """A view that always succeeds."""
    return HttpResponse("ok")


def test_require_htmx_rejects_a_plain_request(rf: RequestFactory) -> None:
    """A fragment fetched as a page is a 400."""
    assert require_htmx(ok)(rf.get("/")).status_code == 400


def test_require_htmx_allows_htmx(rf: RequestFactory) -> None:
    """Htmx's HX-Request header lets the request through."""
    assert require_htmx(ok)(rf.get("/", headers={"HX-Request": "true"})).status_code == 200


def test_login_required_json_answers_401(rf: RequestFactory) -> None:
    """Signed out is a 401, never a redirect that fetch() would follow to a 200."""
    request = rf.post("/")
    request.user = AnonymousUser()
    response = login_required_json(ok)(request)
    assert response.status_code == 401
