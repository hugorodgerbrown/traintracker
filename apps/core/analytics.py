"""Visitor counts by fivebar (https://fiveb.ar): cookieless web analytics.

Off unless ``FIVEBAR_DOMAIN`` is set (titan sets it at go-live to the
app's domain). Then every page, public or in the app, carries fivebar's
tag in its ``<head>`` (``templates/includes/_analytics.html``) and the CSP
lets its script load and send. There is no package: the script counts
each page by itself. MCP tool calls, which have no page, are sent from
the server as events instead (apps/mcp/events.py).

A page whose address holds a secret or an id (an emailed sign-in link's
token, a record's uuid) is counted under its route instead, with the
values named, e.g. ``/signin/link/:token/``, so nothing private reaches
fivebar.

fivebar counts pages by itself. An event (an account's first sign-in) is
marked by ending a redirect's address with a fragment, which
``static/js/analytics.js`` sends as the event and takes out of the address.
See docs/decisions/visitors-are-counted-by-fivebar.md.
"""

from __future__ import annotations

import re
from functools import cache
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.urls import Resolver404, URLResolver, get_resolver, resolve

if TYPE_CHECKING:
    # Only for annotations: config/settings.py imports this module.
    from django.http import HttpRequest

# Where the script loads from, and where it sends its counts.
ORIGIN = "https://fiveb.ar"
SCRIPT = f"{ORIGIN}/js/tally.js"

# The events, by the fragment that marks them. Keep in step with
# static/js/analytics.js: EVENTS.
SIGNED_UP = "#signed-up"  # "Sign up": an account's first sign-in

# A route's converters, "<str:token>" or "<token>": each becomes ":token".
_CONVERTER = re.compile(r"<(?:\w+:)?(\w+)>")


def csp_sources(domain: str) -> list[str]:
    """The sources script-src and connect-src add for fivebar: none while it is off."""
    return [ORIGIN] if domain else []


def mark(url: str, event: str) -> str:
    """Return ``url`` marked with an event's fragment, while fivebar is on.

    Off, or when the address already has a fragment of its own, it is
    returned as it is: the event is lost rather than the fragment.
    """
    if not settings.FIVEBAR_DOMAIN or "#" in url:
        return url
    return url + event


def counted_path(request: HttpRequest) -> str:
    """The path to count a page under when its address holds values, else "".

    ``/signin/link/abc123/`` is counted as ``/signin/link/:token/``. A page
    whose route takes no values is counted under its own path, so this is
    empty and the tag carries no ``data-path``.

    An error raised before the URL was resolved (a 500 in middleware, a
    ``DisallowedHost`` 400) has no ``resolver_match``, so the route is
    looked up here. An address no route matches (only ever a 404) is
    counted as ``/signin/:unmatched/`` when its first segment begins one of
    the app's routes, else as ``/:unmatched/``: a mangled sign-in link
    still holds its token, and no route is there to name it.
    """
    urlconf = getattr(request, "urlconf", None)
    match = request.resolver_match
    if match is None:
        try:
            match = resolve(request.path_info, urlconf)
        except Resolver404:
            first = request.path_info.strip("/").partition("/")[0]
            known = first in route_prefixes(urlconf or settings.ROOT_URLCONF)
            return f"/{first}/:unmatched/" if known else "/:unmatched/"
    if not (match.kwargs or match.args):
        return ""
    return "/" + _CONVERTER.sub(r":\1", match.route)


@cache
def route_prefixes(urlconf: str) -> frozenset[str]:
    """The literal first segments the app's routes begin with: ``signin``, ``app``.

    Only these are safe to send for an unmatched address; any other first
    segment could be a value (a token whose prefix was lost).
    """
    prefixes: set[str] = set()

    def walk(patterns: list[Any]) -> None:
        for entry in patterns:
            route = str(entry.pattern).lstrip("^")
            if not route and isinstance(entry, URLResolver):
                walk(entry.url_patterns)
                continue
            first = route.partition("/")[0]
            if re.fullmatch(r"[\w.-]+", first):
                prefixes.add(first)

    walk(get_resolver(urlconf).url_patterns)
    return frozenset(prefixes)


def counts_mcp() -> bool:
    """Whether MCP tool calls are sent to fivebar (apps/mcp/events.py).

    ``FIVEBAR_TOKEN`` is only a setting in a project with the MCP endpoint.
    """
    return bool(settings.FIVEBAR_DOMAIN and getattr(settings, "FIVEBAR_TOKEN", ""))


def analytics(request: HttpRequest) -> dict[str, dict[str, Any]]:
    """Expose fivebar's settings to every template as ``analytics``."""
    domain = settings.FIVEBAR_DOMAIN
    return {
        "analytics": {
            "domain": domain,
            "script": SCRIPT,
            "path": counted_path(request) if domain else "",
            "counts_mcp": counts_mcp(),
        }
    }
