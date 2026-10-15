"""Keep signed-in pages out of the browser's HTTP cache.

A response to a signed-in user that doesn't say how to cache itself gets
``Cache-Control: no-store`` (Django's ``never_cache`` headers), so after
signing out, Back can't show the last user's pages and nothing has to be
cleared. This replaces ``Clear-Site-Data: "cache"`` on sign-out, which
Chrome takes many seconds to honour (it walks the whole disk cache) and so
held the redirect to the sign-in page.

- Middleware rather than a decorator, so a new signed-in page, or one a
  package serves (mcp-auth's consent page), can't forget it.
- A view that sets its own ``Cache-Control`` keeps it: the public pages
  and boards say what they mean for everyone.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from django.http import HttpRequest, HttpResponse
from django.utils.cache import add_never_cache_headers

logger = logging.getLogger(__name__)


class PrivatePagesMiddleware:
    """Mark signed-in responses ``no-store`` unless the view chose otherwise."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        """Keep the next handler in the chain."""
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Add the never-cache headers to a signed-in user's uncached response."""
        response = self.get_response(request)
        # Check the header first: reading request.user loads the session from
        # the database, which /livez (never_cache) must not do.
        if not response.has_header("Cache-Control") and request.user.is_authenticated:
            add_never_cache_headers(response)
        return response
