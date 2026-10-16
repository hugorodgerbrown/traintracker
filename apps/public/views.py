"""The site's pages: the homepage, docs, privacy and terms, and the favicon.

Each page's content is a fragment in ``templates/public/pages/`` (pages.PAGES),
placed in ``public/layout.html``. The fields the fragments name (``mcp_url``,
``fair_use``) are this server's own values.
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

from apps.public import pages
from apps.rail.settings import rail_settings

logger = logging.getLogger(__name__)

# The footer's links. Home is the icon and name at the header's left.
NAV = [
    *((path, text) for path, (_, text, _, _) in pages.PAGES.items() if path != "/"),
    *pages.EXTRA_NAV.items(),
]


def relative(path: str) -> str:
    """A site path without its leading slash, for a template's ``href="/{{ path }}"``.

    Every link the templates write starts with a literal ``/``, so no value
    can turn it into another scheme (``javascript:``).
    """
    return path.removeprefix("/")


@cache
def fields() -> dict[str, str]:
    """The values the pages name: the MCP address and the fair-use line."""
    return pages.fields(rail_settings(), settings.SITE_URL)


def page_context(path: str, title: str, description: str, current: str) -> dict[str, Any]:
    """What every page inside the layout needs: titles, sharing and the footer's links."""
    return {
        "title": title,
        "description": description,
        "page_url": f"{settings.SITE_URL}{path}",
        "share_image": f"{settings.SITE_URL}{static('img/share.png')}",
        "share_image_alt": pages.SHARE_IMAGE_ALT,
        "nav": [(relative(p), text, p == current) for p, text in NAV],
        "current": current,
        **fields(),
    }


@require_safe
@cache_control(public=True, max_age=300)
def page(request: HttpRequest, path: str) -> HttpResponse:
    """One of the site's pages, by its path ("/", "/docs", ...)."""
    if path not in pages.PAGES:
        raise Http404
    name, _, title, description = pages.PAGES[path]
    context = page_context(path, title, description, path) | {"fragment": name}
    return render(request, "public/page.html", context)


@require_safe
@cache_control(public=True, max_age=86400)
def favicon(request: HttpRequest) -> HttpResponse:
    """Browsers ask for /favicon.ico whatever the page links to: the PNG."""
    return redirect(static("icons/icon-32.png"), permanent=True)
