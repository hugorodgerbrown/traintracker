"""ChatGPT connects by its client metadata document (CIMD)."""

import json
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from django.core.cache import cache
from django.test import Client
from mcp_auth.testing import pkce_pair
from oauth2_provider import cimd
from oauth2_provider.models import get_application_model

from tests.factories import UserFactory

CHATGPT_CLIENT_ID = "https://chatgpt.com/oauth/client.json"
CHATGPT_CALLBACK = "https://chatgpt.com/connector_platform_oauth_redirect"
# The fields of ChatGPT's published document that the toolkit reads, as
# reported in django-oauth-toolkit issue 1845 (October 2026). It prefers
# private_key_jwt but can also use none.
CHATGPT_METADATA = {
    "client_id": CHATGPT_CLIENT_ID,
    "client_name": "ChatGPT",
    "redirect_uris": [CHATGPT_CALLBACK],
    "grant_types": ["authorization_code", "refresh_token"],
    "response_types": ["code"],
    "token_endpoint_auth_method": "private_key_jwt",
    "token_endpoint_auth_methods_supported": ["none", "private_key_jwt"],
    "token_endpoint_auth_signing_alg": "RS256",
    "jwks_uri": "https://chatgpt.com/oauth/jwks.json",
}
MCP_URL = "http://testserver/mcp"


@pytest.fixture(autouse=True)
def _fresh_cache() -> None:
    """Rate limits and the CIMD failure backoff live in the cache; start with none."""
    cache.clear()


def serve(monkeypatch: pytest.MonkeyPatch, metadata: dict[str, Any]) -> None:
    """Answer every CIMD fetch with ``metadata``."""
    monkeypatch.setattr(cimd.SafeMetadataFetcher, "fetch", lambda self, client_id: (metadata, 3600))


def authorize_params(challenge: str) -> dict[str, str]:
    """The authorize query ChatGPT sends."""
    return {
        "response_type": "code",
        "client_id": CHATGPT_CLIENT_ID,
        "redirect_uri": CHATGPT_CALLBACK,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": "xyz",
        "scope": "mcp offline_access",
        "resource": MCP_URL,
    }


@pytest.mark.django_db
def test_chatgpt_connects_as_a_public_client(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Consent, then the code swaps for a token on PKCE alone, with no client assertion."""
    serve(monkeypatch, CHATGPT_METADATA)
    verifier, challenge = pkce_pair()
    client.force_login(UserFactory.create())
    page = client.get("/oauth/authorize/", authorize_params(challenge))
    assert page.status_code == 200, page.content

    form = page.context["form"].initial
    approved = client.post(
        "/oauth/authorize/", {**{k: v for k, v in form.items() if v}, "allow": "Authorize"}
    )
    assert approved.status_code == 302
    back = urlparse(approved["Location"])
    assert f"{back.scheme}://{back.netloc}{back.path}" == CHATGPT_CALLBACK
    client.logout()

    tokens = client.post(
        "/oauth/token/",
        {
            "grant_type": "authorization_code",
            "code": parse_qs(back.query)["code"][0],
            "redirect_uri": CHATGPT_CALLBACK,
            "client_id": CHATGPT_CLIENT_ID,
            "code_verifier": verifier,
            "resource": MCP_URL,
        },
    )
    assert tokens.status_code == 200, tokens.content
    app = get_application_model().objects.get(client_id=CHATGPT_CLIENT_ID)
    assert app.client_type == "public"
    ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    called = client.post(
        "/mcp",
        json.dumps(ping),
        "application/json",
        headers={"Authorization": f"Bearer {tokens.json()['access_token']}"},
    )
    assert called.status_code == 200


@pytest.mark.django_db
@pytest.mark.parametrize(
    "changes",
    [
        pytest.param({"token_endpoint_auth_methods_supported": ["private_key_jwt"]}, id="no-none"),
        pytest.param({"token_endpoint_auth_methods_supported": None}, id="no-list"),
        pytest.param({"token_endpoint_auth_methods_supported": "none"}, id="not-a-list"),
        pytest.param({"client_secret": "shh"}, id="secret"),
    ],
)
def test_a_client_that_cannot_go_public_is_refused(
    client: Client, monkeypatch: pytest.MonkeyPatch, changes: dict[str, Any]
) -> None:
    """Only a client that lists none is taken as public; the rest stay refused."""
    metadata = {k: v for k, v in (CHATGPT_METADATA | changes).items() if v is not None}
    serve(monkeypatch, metadata)
    _, challenge = pkce_pair()
    client.force_login(UserFactory.create())
    page = client.get("/oauth/authorize/", authorize_params(challenge))
    assert b"Invalid client_id parameter value" in page.content
    assert not get_application_model().objects.filter(client_id=CHATGPT_CLIENT_ID).exists()


def test_the_fallback_wraps_the_toolkit_once() -> None:
    """Running app setup again leaves one wrapper, not a stack of them."""
    from apps.mcp.cimd import accept_public_fallback

    wrapped = cimd._build_application_kwargs
    accept_public_fallback()
    assert cimd._build_application_kwargs is wrapped
