"""Tests for apps.core.analytics: fivebar's tag, its CSP sources and counted paths."""

from typing import Any

from django.test import Client, RequestFactory
from django.urls import resolve, reverse

from apps.core.analytics import (
    ORIGIN,
    SCRIPT,
    SIGNED_UP,
    analytics,
    counted_path,
    csp_sources,
    mark,
)

DOMAIN = "example.ultraniche.ai"


def test_csp_sources_are_empty_while_off() -> None:
    """No FIVEBAR_DOMAIN, no extra origin in the policy."""
    assert csp_sources("") == []
    assert csp_sources(DOMAIN) == [ORIGIN]


def test_the_default_policy_leaves_fivebar_out(client: Client, db: None) -> None:
    """The shipped settings (FIVEBAR_DOMAIN unset) allow no other origin."""
    csp = client.get(reverse("accounts:sign_in"))["Content-Security-Policy"]
    assert ORIGIN not in csp


def test_counted_path() -> None:
    """A route's values are named; a route without values needs no data-path."""
    factory = RequestFactory()
    request = factory.get("/signin/link/abc/")
    request.resolver_match = resolve("/signin/link/abc/")
    assert counted_path(request) == "/signin/link/:token/"
    request = factory.get("/signin/")
    request.resolver_match = resolve("/signin/")
    assert counted_path(request) == ""
    assert counted_path(factory.get("/nowhere/")) == ""


def test_context_has_no_path_while_off(settings: Any) -> None:
    """Off, the context names nothing, so the tag isn't drawn."""
    settings.FIVEBAR_DOMAIN = ""
    request = RequestFactory().get("/signin/link/abc/")
    request.resolver_match = resolve("/signin/link/abc/")
    assert analytics(request)["analytics"] == {
        "domain": "",
        "script": SCRIPT,
        "path": "",
        "counts_mcp": False,
    }


def test_mark(settings: Any) -> None:
    """An event's fragment is added only while on, and never over another fragment."""
    settings.FIVEBAR_DOMAIN = ""
    assert mark("/app/", SIGNED_UP) == "/app/"
    settings.FIVEBAR_DOMAIN = DOMAIN
    assert mark("/app/", SIGNED_UP) == "/app/#signed-up"
    assert mark("/app/#top", SIGNED_UP) == "/app/#top"
