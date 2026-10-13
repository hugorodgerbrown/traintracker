"""The site's pages: the homepage, docs, privacy and terms, and the favicon.

Each page's content is production's own fragment in ``src/traintracker/site/``
(on the template path, settings.TEMPLATES), placed in ``public/layout.html``:
one copy of the words until Titan 4/4 moves them into this app. The fields
the fragments name (``mcp_url``, ``fair_use``) are the MCP server's own
values, as production fills them in.
"""

import logging
from functools import cache
from typing import Any

from django.conf import settings
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.templatetags.static import static
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_safe

from apps.rail.settings import rail_settings
from traintracker import http_app, site

logger = logging.getLogger(__name__)

# The header's links after Home, which is the icon and name at the left.
NAV = [
    *((path, text) for path, (_, text, _, _) in site.PAGES.items() if path != "/"),
    *site.EXTRA_NAV.items(),
]


@cache
def fields() -> dict[str, str]:
    """The values the pages name: the MCP address and the fair-use line."""
    return http_app.site_fields(rail_settings())


def page_context(path: str, title: str, description: str, current: str) -> dict[str, Any]:
    """What every page inside the layout needs: titles, sharing and the header."""
    return {
        "title": title,
        "description": description,
        "page_url": f"{settings.SITE_URL}{path}",
        "share_image": f"{settings.SITE_URL}{static('img/share.png')}",
        "share_image_alt": site.SHARE_IMAGE_ALT,
        "nav": NAV,
        "current": current,
        **fields(),
    }


@require_safe
@cache_control(public=True, max_age=300)
def page(request: HttpRequest, path: str) -> HttpResponse:
    """One of the site's pages, by its path ("/", "/docs", ...)."""
    if path not in site.PAGES:
        raise Http404
    name, _, title, description = site.PAGES[path]
    context = page_context(path, title, description, path) | {"fragment": name}
    return render(request, "public/page.html", context)


@require_safe
@cache_control(public=True, max_age=86400)
def favicon(request: HttpRequest) -> HttpResponse:
    """Browsers ask for /favicon.ico whatever the page links to: the PNG."""
    return redirect(static("icons/icon-32.png"), permanent=True)


@require_safe
def openai_challenge(request: HttpRequest) -> HttpResponse:
    """The token OpenAI's portal issued to prove the domain, as bare text."""
    token = rail_settings().openai_apps_challenge
    if not token:
        raise Http404
    return HttpResponse(token, content_type="text/plain; charset=utf-8")
