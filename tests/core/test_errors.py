"""Tests for apps.core.errors: every error, in every shape it is sent."""

import html
import json
from typing import Any
from unittest import mock

import pytest
from django.db import connection
from django.test import Client, RequestFactory
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.core import errors
from apps.core.analytics import SCRIPT

pytestmark = [pytest.mark.django_db, pytest.mark.urls("tests.core.error_urls")]

JSON = {"Accept": "application/json"}
HTMX = {"HX-Request": "true"}


@pytest.fixture
def anyone() -> Client:
    """A browser that gets the error page rather than the exception."""
    return Client(raise_request_exception=False)


def page(response: Any) -> str:
    """A response's body as text, entities decoded (the copy has apostrophes)."""
    return html.unescape(response.content.decode())


# --- The website's frame: 403, CSRF and 404 --------------------------------


def test_a_missing_page_is_a_404_on_the_websites_frame(anyone: Client) -> None:
    """The reader keeps the header, a way home and the footer."""
    response = anyone.get("/no-such-page/")
    assert response.status_code == 404
    body = page(response)
    assert "Page not found" in body
    assert "Error 404" in body
    assert "Go to the front page" in body
    assert 'class="name"' in body
    assert "Powered by National Rail Enquiries" in body
    assert "Sign in" in body


def test_signed_in_the_404_keeps_the_way_into_the_app(anyone: Client, user: Any) -> None:
    """The header still offers the app to someone signed in."""
    anyone.force_login(user)
    assert "Your account" in page(anyone.get("/no-such-page/"))


def test_a_raised_permission_denied_is_the_403_page(anyone: Client) -> None:
    """PermissionDenied gets the 403 copy on the website's frame."""
    response = anyone.get("/raise/403/")
    assert response.status_code == 403
    assert "You can't open this page" in page(response)
    assert "Powered by National Rail Enquiries" in page(response)


def test_a_site_page_that_fails_falls_back_to_the_standalone_one(anyone: Client) -> None:
    """If the frame itself breaks, the reader still gets a page, not a 500."""
    with mock.patch("apps.core.errors.render", side_effect=RuntimeError("frame")):
        response = anyone.get("/no-such-page/")
    assert response.status_code == 404
    assert "Page not found" in page(response)
    assert 'name="robots" content="noindex"' in page(response)


# --- Standalone: 400 and 500 -------------------------------------------------


def test_a_500_is_the_standalone_page(anyone: Client, user: Any) -> None:
    """The 500 has the header and footer, but nothing that needs the session."""
    anyone.force_login(user)
    response = anyone.get("/raise/500/")
    assert response.status_code == 500
    body = page(response)
    assert "Something went wrong on our side" in body
    assert "Error 500" in body
    assert "Powered by National Rail Enquiries" in body
    assert "Your account" not in body


def test_the_500_reads_no_session_or_database(rf: RequestFactory) -> None:
    """The handler alone: no query, and no ``request.user`` (this request has none)."""
    request = rf.get("/raise/500/")
    with CaptureQueriesContext(connection) as queries:
        response = errors.server_error(request)
    assert response.status_code == 500
    assert "Something went wrong on our side" in page(response)
    assert len(queries) == 0


def test_a_suspicious_request_is_the_standalone_400(anyone: Client) -> None:
    """SuspiciousOperation is a 400, drawn without the request."""
    response = anyone.get("/raise/400/")
    assert response.status_code == 400
    assert "This request couldn't be read" in page(response)


def test_a_disallowed_host_is_the_standalone_400(anyone: Client) -> None:
    """A bad Host header is a 400 too, where building any absolute address would raise again."""
    response = anyone.get("/", HTTP_HOST="evil.example")
    assert response.status_code == 400
    assert "This request couldn't be read" in page(response)


def test_the_standalone_page_falls_back_to_plain_html(anyone: Client) -> None:
    """When even the template fails (no static manifest), a literal page is sent."""
    with mock.patch("apps.core.errors.render_to_string", side_effect=ValueError("manifest")):
        response = anyone.get("/raise/500/")
    assert response.status_code == 500
    assert page(response) == errors.FALLBACK_HTML


# --- fivebar's data-status ---------------------------------------------------

FIVEBAR = "example.ultraniche.ai"


@pytest.mark.parametrize(
    ("path", "status"),
    [
        ("/no-such-page/", 404),
        ("/raise/403/", 403),
        ("/raise/400/", 400),
        ("/raise/500/", 500),
    ],
)
def test_every_error_page_tells_fivebar_its_status(
    anyone: Client, settings: Any, path: str, status: int
) -> None:
    """Safari reports no status, so the tag carries it (https://fiveb.ar/docs/error-pages)."""
    settings.FIVEBAR_DOMAIN = FIVEBAR
    body = anyone.get(path).content.decode()
    assert body.count(SCRIPT) == 1
    assert f'data-domain="{FIVEBAR}"' in body
    assert f'data-status="{status}"' in body


def test_the_csrf_page_tells_fivebar_its_403(settings: Any) -> None:
    """The CSRF failure is a 403 like any other."""
    settings.FIVEBAR_DOMAIN = FIVEBAR
    client = Client(enforce_csrf_checks=True)
    body = client.post(reverse("accounts:sign_in"), {"email": "a@example.com"}).content.decode()
    assert 'data-status="403"' in body


def test_the_standalone_500_counts_a_token_under_its_route(anyone: Client, settings: Any) -> None:
    """The tag on the 500 is built from the route, so a value in the address stays out."""
    settings.FIVEBAR_DOMAIN = FIVEBAR
    body = anyone.get("/raise/500/secret-token/").content.decode()
    assert 'data-path="/raise/500/:token/"' in body
    assert 'data-status="500"' in body
    assert "secret-token" not in body


def test_an_unmatched_404_keeps_a_mangled_token_out(anyone: Client, settings: Any) -> None:
    """No route names the token in a mangled sign-in link, so only its first segment is sent."""
    settings.FIVEBAR_DOMAIN = FIVEBAR
    body = anyone.get("/signin/link/secret-token/extra").content.decode()
    assert 'data-status="404"' in body
    assert 'data-path="/signin/:unmatched/"' in body
    assert "secret-token" not in body


def test_error_pages_carry_no_tag_while_fivebar_is_off(anyone: Client) -> None:
    """Off, neither kind of error page loads the script."""
    for path in ("/no-such-page/", "/raise/500/"):
        assert SCRIPT not in anyone.get(path).content.decode()


# --- JSON and htmx -----------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "status", "slug"),
    [
        ("/no-such-page/", 404, "not_found"),
        ("/raise/403/", 403, "forbidden"),
        ("/raise/400/", 400, "bad_request"),
        ("/raise/500/", 500, "server_error"),
    ],
)
def test_a_client_that_wants_json_gets_json(
    anyone: Client, path: str, status: int, slug: str
) -> None:
    """An API client learns what went wrong without parsing a page."""
    response = anyone.get(path, headers=JSON)
    assert response.status_code == status
    assert response["Content-Type"] == "application/json"
    assert json.loads(response.content) == {"error": slug, "status": status}


@pytest.mark.parametrize("path", ["/.well-known/no-such-document", "/mcp/no-such-tool"])
def test_mcp_and_oauth_paths_get_json_whatever_they_accept(anyone: Client, path: str) -> None:
    """The MCP endpoint and the discovery documents always answer in JSON."""
    response = anyone.get(path, headers={"Accept": "text/html,*/*"})
    assert response.status_code == 404
    assert json.loads(response.content)["error"] == "not_found"


def test_a_failing_mcp_call_gets_a_json_500(anyone: Client) -> None:
    """A 500 under /mcp is JSON, not the page."""
    response = anyone.get("/mcp/raise/500/")
    assert response.status_code == 500
    assert json.loads(response.content) == {"error": "server_error", "status": 500}


def test_a_browser_accepting_anything_gets_the_page(anyone: Client) -> None:
    """``*/*`` accepts JSON and HTML alike, so it gets HTML."""
    response = anyone.get("/no-such-page/", headers={"Accept": "*/*"})
    assert response["Content-Type"].startswith("text/html")


def test_htmx_gets_the_heading_as_plain_text(anyone: Client) -> None:
    """An htmx request gets one line, since htmx doesn't swap an error."""
    response = anyone.get("/no-such-page/", headers=HTMX)
    assert response.status_code == 404
    assert response["Content-Type"] == "text/plain; charset=utf-8"
    assert page(response) == "Page not found"


@pytest.mark.parametrize("headers", [{}, JSON, HTMX])
def test_every_shape_varies_on_accept_and_htmx(anyone: Client, headers: dict[str, str]) -> None:
    """One address answers in three shapes, so a cache must keep them apart."""
    vary = anyone.get("/no-such-page/", headers=headers)["Vary"]
    assert "Accept" in vary
    assert "HX-Request" in vary


# --- CSRF ---------------------------------------------------------------------


def test_a_csrf_failure_is_the_expired_form_page(user: Any) -> None:
    """A form posted with a stale token says how to recover."""
    client = Client(enforce_csrf_checks=True)
    client.force_login(user)
    response = client.post(reverse("accounts:sign_in"), {"email": "a@example.com"})
    assert response.status_code == 403
    assert "This form has expired" in page(response)
    assert response["X-CSRF-Failure"] == "1"


def test_a_csrf_failure_is_marked_for_the_outbox_in_json(user: Any) -> None:
    """The outbox keeps a write refused with X-CSRF-Failure, whatever the shape."""
    client = Client(enforce_csrf_checks=True)
    client.force_login(user)
    response = client.post(
        reverse("accounts:sign_in"), "{}", content_type="application/json", headers=JSON
    )
    assert response.status_code == 403
    assert response["X-CSRF-Failure"] == "1"
    assert json.loads(response.content) == {"error": "csrf_failed", "status": 403}
