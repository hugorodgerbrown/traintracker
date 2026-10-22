"""The site's error pages: handler400/403/404/500 and the CSRF failure view.

Wired in ``config/urls.py`` (the four handlers) and ``config/settings.py``
(``CSRF_FAILURE_VIEW``). Each answers in the shape the client asked for:

- **JSON**, ``{"error": "not_found", "status": 404}``, under ``/mcp`` and
  ``/.well-known/``, and for a request that accepts JSON but not HTML. An
  MCP or OAuth client should never have to parse a page.
- **Plain text**, the heading, for an htmx request: htmx doesn't swap a
  4xx or 5xx, so a whole page would be bytes nobody sees.
- **HTML** for everyone else.

The HTML comes in two kinds, and the split is the point of this module
(docs/decisions/the-500-page-renders-without-context.md):

- **403, CSRF and 404** render ``errors/page.html`` on the website's frame
  (``site_base.html``), with the request, so the reader keeps the header,
  their signed-in state and the footer. If that render fails, they drop to
  the standalone page rather than turning into a 500.
- **400 and 500** render ``errors/standalone.html`` from a plain dict: no
  context processors, no ``request.user``, no session, no database. A 500
  is often caused by one of those, and a 400 can be ``DisallowedHost``.
  If even that fails (a missing static manifest), ``FALLBACK_HTML``.

Both kinds carry fivebar's tag while it is on, with ``data-status`` set to
the response's status (``http_status``, as ``apps.core.shortcuts.render``
sets it on every page): Safari doesn't report a page's status itself, so
without it fivebar counts a Safari 404 as a pageview
(https://fiveb.ar/docs/error-pages).

The copy lives here, in ``ERROR_PAGES``, so both kinds say the same thing.
With ``DEBUG`` on Django shows its technical pages instead. Traintrackr has
no design page, so there is no staff preview: run with ``DEBUG`` off to see
one.
"""

import dataclasses
import logging

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.template.loader import render_to_string
from django.utils.cache import patch_vary_headers
from django.views.decorators.csrf import requires_csrf_token

from apps.core.analytics import analytics
from apps.core.shortcuts import render
from apps.public.views import NAV, relative
from apps.pwa import conf as pwa_conf

logger = logging.getLogger(__name__)

SITE_TEMPLATE = "errors/page.html"
STANDALONE_TEMPLATE = "errors/standalone.html"

# Paths whose clients speak JSON, whatever their Accept header says: the MCP
# endpoint and the OAuth discovery documents.
MCP_PATH = "/mcp"
JSON_PREFIXES = ("/mcp/", "/.well-known/")

# The last resort, when the standalone template can't render. No assets and
# no template engine: by now neither can be trusted.
FALLBACK_HTML = (
    "<!doctype html><html lang='en-GB'><head><meta charset='utf-8'>"
    "<meta name='viewport' content='width=device-width, initial-scale=1'>"
    "<title>Something went wrong</title></head><body>"
    "<h1>Something went wrong on our side</h1>"
    "<p>This page couldn't load. Try again in a minute.</p>"
    "<p><a href='/'>Go to the front page</a></p></body></html>"
)


@dataclasses.dataclass(frozen=True)
class ErrorPage:
    """One error, in every shape it is sent.

    Attributes:
        status: The HTTP status.
        slug: The JSON body's ``error``.
        heading: The page's ``<h1>``, and the htmx plain-text body.
        body: One line under the heading.
        site: Whether the HTML page renders on the website's frame, with
            the request; otherwise it is only ever the standalone page.
    """

    status: int
    slug: str
    heading: str
    body: str
    site: bool


ERROR_PAGES: dict[str, ErrorPage] = {
    "400": ErrorPage(
        status=400,
        slug="bad_request",
        heading="This request couldn't be read",
        body="Something in the address or the form didn't make sense. Go back and try again.",
        site=False,
    ),
    "403": ErrorPage(
        status=403,
        slug="forbidden",
        heading="You can't open this page",
        body="It may belong to another account. Check which one you're signed in with.",
        site=True,
    ),
    "csrf": ErrorPage(
        status=403,
        slug="csrf_failed",
        heading="This form has expired",
        body=(
            "Reload the page and send it again. This happens when a page has been "
            "open a long time, or when cookies are blocked."
        ),
        site=True,
    ),
    "404": ErrorPage(
        status=404,
        slug="not_found",
        heading="Page not found",
        body="The link may be mistyped or out of date.",
        site=True,
    ),
    "500": ErrorPage(
        status=500,
        slug="server_error",
        heading="Something went wrong on our side",
        body="This page couldn't load. Try again in a minute.",
        site=False,
    ),
}


def wants_json(request: HttpRequest) -> bool:
    """Whether the client gets a JSON body.

    A browser's ``*/*`` accepts both JSON and HTML, so it gets the page.
    """
    if request.path == MCP_PATH or request.path.startswith(JSON_PREFIXES):
        return True
    return request.accepts("application/json") and not request.accepts("text/html")


def standalone(page: ErrorPage, request: HttpRequest) -> str:
    """The page rendered without the request: no context processors, no database.

    ``pwa`` is the app's name and ``nav`` the footer's links, both read from
    config, not from the request. ``analytics`` is fivebar's tag, from
    settings and the matched route alone: it reads neither the session nor
    the host, so it can't fail the way a 500 or a ``DisallowedHost`` 400 did.
    """
    context = {
        "page": page,
        "pwa": {"name": pwa_conf.APP_NAME},
        "nav": [(relative(path), text, False) for path, text in NAV],
        "http_status": page.status,
    }
    try:
        context.update(analytics(request))
        return render_to_string(STANDALONE_TEMPLATE, context)
    except Exception:
        logger.exception("errors: the standalone page failed; sending the fallback")
        return FALLBACK_HTML


def _negotiate(request: HttpRequest, page: ErrorPage) -> HttpResponse:
    """JSON, plain text or HTML for ``page``: see the module docstring."""
    if wants_json(request):
        return JsonResponse({"error": page.slug, "status": page.status}, status=page.status)
    if request.headers.get("HX-Request") == "true":
        return HttpResponse(
            page.heading, status=page.status, content_type="text/plain; charset=utf-8"
        )
    if page.site:
        try:
            return render(request, SITE_TEMPLATE, {"page": page}, status=page.status)
        except Exception:
            logger.exception("errors: the %s page failed; sending the standalone one", page.slug)
    return HttpResponse(standalone(page, request), status=page.status)


def error_response(request: HttpRequest, kind: str) -> HttpResponse:
    """The response for one kind of error (a key of ``ERROR_PAGES``).

    One address can answer in any of three shapes, so every one varies on
    ``Accept`` and ``HX-Request``: a cache must not hand a browser the JSON
    404 it kept for an API client.
    """
    response = _negotiate(request, ERROR_PAGES[kind])
    patch_vary_headers(response, ("Accept", "HX-Request"))
    if kind == "csrf":
        # The outbox keeps a write refused with this and retries after the
        # next page load refreshes the token, rather than dropping it.
        response["X-CSRF-Failure"] = "1"
    return response


@requires_csrf_token
def bad_request(request: HttpRequest, exception: Exception) -> HttpResponse:
    """``handler400``: a malformed request, ``SuspiciousOperation``, a bad host."""
    return error_response(request, "400")


@requires_csrf_token
def permission_denied(request: HttpRequest, exception: Exception) -> HttpResponse:
    """``handler403``: a raised ``PermissionDenied``.

    A view that returns its own 403 doesn't come here, and keeps its body.
    """
    return error_response(request, "403")


@requires_csrf_token
def page_not_found(request: HttpRequest, exception: Exception) -> HttpResponse:
    """``handler404``: no URL matched, or a view raised ``Http404``."""
    return error_response(request, "404")


@requires_csrf_token
def server_error(request: HttpRequest) -> HttpResponse:
    """``handler500``: an unhandled exception, already logged by ``django.request``."""
    return error_response(request, "500")


def csrf_failure(request: HttpRequest, reason: str = "") -> HttpResponse:
    """``CSRF_FAILURE_VIEW``: a write whose CSRF check failed.

    A queued write can carry a token that went stale while it waited (the
    user signed in again in another tab), so the response is marked with
    ``X-CSRF-Failure`` for the outbox. The reason is logged, never shown.
    """
    logger.warning("errors: CSRF failure on %s: %s", request.path, reason)
    return error_response(request, "csrf")
