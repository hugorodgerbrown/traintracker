"""Tests for apps.core.analytics: fivebar's tag, its CSP sources and counted paths."""

from typing import Any

import pytest
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


@pytest.mark.django_db
def test_no_tag_while_off(client: Client) -> None:
    """Pages carry no fivebar tag until FIVEBAR_DOMAIN is set."""
    html = client.get(reverse("public:home")).content.decode()
    assert SCRIPT not in html


@pytest.mark.django_db
def test_a_public_page_carries_its_status(client: Client, settings: Any) -> None:
    """One async tag, with the site's domain and the page's status."""
    settings.FIVEBAR_DOMAIN = DOMAIN
    html = client.get(reverse("public:home")).content.decode()
    tag = f'<script async data-domain="{DOMAIN}" data-status="200" src="{SCRIPT}"></script>'
    assert html.count(SCRIPT) == 1
    assert tag in html


@pytest.mark.django_db
def test_the_sign_in_pages_never_load_the_tag(client: Client, settings: Any) -> None:
    """One-time addresses: no fivebar, even with a status to report."""
    settings.FIVEBAR_DOMAIN = DOMAIN
    html = client.post(reverse("accounts:sign_in"), {"email": "not an address"}).content.decode()
    assert SCRIPT not in html


def test_counted_path() -> None:
    """A route's values are named; a route without values needs no data-path."""
    factory = RequestFactory()
    request = factory.get("/signin/link/abc/")
    request.resolver_match = resolve("/signin/link/abc/")
    assert counted_path(request) == "/signin/link/:token/"
    request = factory.get("/signin/")
    request.resolver_match = resolve("/signin/")
    assert counted_path(request) == ""
    assert counted_path(factory.get("/nowhere/")) == "/:unmatched/"


def test_counted_path_without_a_resolved_route() -> None:
    """An error before resolution looks the route up; an unmatched address keeps one segment."""
    factory = RequestFactory()
    assert counted_path(factory.get("/signin/link/abc/")) == "/signin/link/:token/"
    assert counted_path(factory.get("/signin/")) == ""
    assert counted_path(factory.get("/signin/link/abc/extra")) == "/signin/:unmatched/"


def test_an_unmatched_first_segment_is_sent_only_when_a_route_begins_with_it() -> None:
    """A token that lost its prefix is the first segment, so an unknown one is never sent."""
    factory = RequestFactory()
    assert counted_path(factory.get("/secret-token/extra")) == "/:unmatched/"
    assert counted_path(factory.get("/secret-token/")) == "/:unmatched/"
    assert counted_path(factory.get("/signin/nowhere/")) == "/signin/:unmatched/"


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
