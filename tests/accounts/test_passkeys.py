"""Tests for passkeys: the service (apps.accounts.passkeys) and its JSON views.

py_webauthn's verifiers are replaced with stubs: a real signature needs a
real authenticator, which the browser journey (tests/e2e/test_passkey.py)
supplies. These tests hold everything around the verification.
"""

import base64
import json
from types import SimpleNamespace
from typing import Any

import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from apps.accounts import passkeys
from apps.accounts.models import Passkey
from tests.factories import PasskeyFactory, UserFactory

pytestmark = pytest.mark.django_db

CREDENTIAL = {"id": "credential-1", "rawId": "credential-1", "type": "public-key", "response": {}}


@pytest.fixture(autouse=True)
def _fresh_rate_limits() -> None:
    """Rate-limit counters live in the cache; start each test with none."""
    cache.clear()


@pytest.fixture
def verified_registration(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every registration verify, as credential ``credential-1``."""
    result = SimpleNamespace(
        credential_id=b"\x01\x02\x03",
        credential_public_key=b"key",
        sign_count=0,
        credential_backed_up=True,
    )
    monkeypatch.setattr(passkeys.webauthn, "verify_registration_response", lambda **_: result)


@pytest.fixture
def verified_sign_in(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every sign-in verify."""
    result = SimpleNamespace(new_sign_count=7, credential_backed_up=True)
    monkeypatch.setattr(passkeys.webauthn, "verify_authentication_response", lambda **_: result)


def post_json(client: Client, url: str, data: Any) -> Any:
    """POST ``data`` as JSON."""
    return client.post(url, json.dumps(data), content_type="application/json")


# ---------- adding a passkey ----------


def test_registration_options_name_the_site_and_skip_my_passkeys(
    signed_in: Client, user: Any, settings: Any
) -> None:
    """Options carry this site, the user, a challenge, and exclude existing passkeys."""
    PasskeyFactory.create(user=user, credential_id="AQID")
    response = signed_in.post(reverse("accounts:passkey_register_options"))
    options = response.json()
    assert options["rp"] == {"id": settings.WEBAUTHN_RP_ID, "name": settings.WEBAUTHN_RP_NAME}
    assert options["user"]["name"] == user.email
    assert options["challenge"]
    assert [c["id"] for c in options["excludeCredentials"]] == ["AQID"]
    assert options["authenticatorSelection"]["userVerification"] == "required"
    assert options["authenticatorSelection"]["residentKey"] == "required"


def test_register_saves_a_passkey_named_for_the_device(
    signed_in: Client, user: Any, verified_registration: None
) -> None:
    """A verified answer becomes a passkey named after the device."""
    signed_in.post(reverse("accounts:passkey_register_options"))
    response = signed_in.post(
        reverse("accounts:passkey_register"),
        json.dumps(CREDENTIAL),
        content_type="application/json",
        headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)"},
    )
    assert response.status_code == 201
    passkey = Passkey.objects.get()
    assert passkey.user == user
    assert passkey.name == "Passkey on iPhone" == response.json()["name"]
    assert passkey.credential_id == "AQID"
    assert passkey.backed_up


def test_register_needs_the_challenge_first(signed_in: Client, verified_registration: None) -> None:
    """Without options there is no challenge to verify against."""
    response = post_json(signed_in, reverse("accounts:passkey_register"), CREDENTIAL)
    assert response.status_code == 400
    assert not Passkey.objects.exists()


def test_register_refuses_a_duplicate(
    signed_in: Client, user: Any, verified_registration: None
) -> None:
    """A credential already saved isn't saved twice."""
    PasskeyFactory.create(credential_id="AQID")
    signed_in.post(reverse("accounts:passkey_register_options"))
    response = post_json(signed_in, reverse("accounts:passkey_register"), CREDENTIAL)
    assert response.status_code == 400


def test_register_refuses_what_does_not_verify(signed_in: Client) -> None:
    """py_webauthn's rejection is a 400, with no detail sent back."""
    signed_in.post(reverse("accounts:passkey_register_options"))
    response = post_json(signed_in, reverse("accounts:passkey_register"), CREDENTIAL)
    assert response.json() == {"error": "not_verified"}


def test_register_refuses_a_body_that_is_not_json(signed_in: Client) -> None:
    """Garbage is a 400."""
    response = signed_in.post(
        reverse("accounts:passkey_register"), "nope", content_type="application/json"
    )
    assert response.status_code == 400


def test_registering_needs_sign_in(client: Client) -> None:
    """Signed out, both endpoints answer 401 JSON."""
    assert client.post(reverse("accounts:passkey_register_options")).status_code == 401
    assert post_json(client, reverse("accounts:passkey_register"), CREDENTIAL).status_code == 401


# ---------- signing in with one ----------


def signed(challenge: str, credential: dict[str, Any] = CREDENTIAL) -> dict[str, Any]:
    """``credential`` as a browser's answer to ``challenge`` (the signature is stubbed)."""
    client_data = json.dumps({"type": "webauthn.get", "challenge": challenge}).encode()
    encoded = base64.urlsafe_b64encode(client_data).decode().rstrip("=")
    return {**credential, "response": {"clientDataJSON": encoded}}


def options(client: Client) -> str:
    """Ask for sign-in options; return the challenge."""
    return str(client.post(reverse("accounts:passkey_sign_in_options")).json()["challenge"])


def answer(client: Client, credential: Any, next_url: str = "") -> Any:
    """Post a sign-in answer."""
    return post_json(
        client, reverse("accounts:passkey_sign_in"), {"credential": credential, "next": next_url}
    )


def sign_in_with(client: Client, credential: Any, next_url: str = "") -> Any:
    """Fetch options, then post ``credential`` signed over their challenge."""
    challenge = options(client)
    if isinstance(credential, dict) and "id" in credential:
        credential = signed(challenge, credential)
    return answer(client, credential, next_url)


def test_sign_in_options_allow_any_passkey(client: Client, settings: Any) -> None:
    """No allowCredentials: the browser offers every passkey it holds for this site."""
    options = client.post(reverse("accounts:passkey_sign_in_options")).json()
    assert options["challenge"]
    assert not options.get("allowCredentials")
    assert options["userVerification"] == "required"
    # passkeys.js signals an unknown passkey under this RP ID.
    assert options["rpId"] == settings.WEBAUTHN_RP_ID


def test_passkey_signs_in_its_owner(client: Client, verified_sign_in: None) -> None:
    """A verified passkey signs its owner in and records the use."""
    passkey = PasskeyFactory.create(credential_id="credential-1")
    response = sign_in_with(client, CREDENTIAL, "/app/account/")
    assert response.json() == {"next": "/app/account/"}
    assert client.session["_auth_user_id"] == str(passkey.user.pk)
    passkey.refresh_from_db()
    assert passkey.sign_count == 7
    assert passkey.last_used_at is not None


def test_unknown_passkey_is_named_so_the_device_can_forget_it(client: Client) -> None:
    """A passkey deleted here answers 404 with its id."""
    response = sign_in_with(client, CREDENTIAL)
    assert response.status_code == 404
    assert response.json() == {"error": "unknown_passkey", "credentialId": "credential-1"}


def test_passkey_that_does_not_verify(client: Client) -> None:
    """A bad signature is a 400 and nobody is signed in."""
    PasskeyFactory.create(credential_id="credential-1")
    response = sign_in_with(client, CREDENTIAL)
    assert response.status_code == 400
    assert "_auth_user_id" not in client.session


def test_passkey_sign_in_needs_the_challenge(client: Client, verified_sign_in: None) -> None:
    """An answer to a challenge this session wasn't given fails."""
    PasskeyFactory.create(credential_id="credential-1")
    assert answer(client, signed("never-issued")).status_code == 400
    options(client)
    assert answer(client, signed("never-issued")).status_code == 400


def test_two_ceremonies_at_once(client: Client, verified_sign_in: None) -> None:
    """Autofill and the button each get a challenge; either answer signs in, once."""
    PasskeyFactory.create(credential_id="credential-1")
    autofill, button = options(client), options(client)
    assert answer(client, signed(autofill)).status_code == 200
    assert answer(client, signed(button)).status_code == 200
    assert answer(client, signed(button)).status_code == 400


def test_a_failed_answer_spends_only_its_own_challenge(client: Client) -> None:
    """A bad signature uses up the challenge it signed and no other."""
    PasskeyFactory.create(credential_id="credential-1")
    first, second = options(client), options(client)
    assert answer(client, signed(first)).status_code == 400
    assert client.session["passkeys.sign_in_challenges"] == [second]


def test_old_challenges_lapse(client: Client, verified_sign_in: None) -> None:
    """Only the last few challenges are kept."""
    PasskeyFactory.create(credential_id="credential-1")
    oldest = options(client)
    for _ in range(passkeys.MAX_SIGN_IN_CHALLENGES):
        options(client)
    assert answer(client, signed(oldest)).status_code == 400


def test_passkey_for_a_closed_account(client: Client, verified_sign_in: None) -> None:
    """An inactive account's passkey doesn't sign in."""
    PasskeyFactory.create(credential_id="credential-1", user=UserFactory.create(is_active=False))
    assert sign_in_with(client, CREDENTIAL).status_code == 403


@pytest.mark.parametrize("body", ["nope", "[]", '{"credential": "x"}'])
def test_passkey_sign_in_refuses_bad_bodies(client: Client, body: str) -> None:
    """Anything but {"credential": {...}} is a 400."""
    response = client.post(
        reverse("accounts:passkey_sign_in"), body, content_type="application/json"
    )
    assert response.status_code == 400


def test_credential_without_an_id(client: Client) -> None:
    """A credential with no id can't be looked up."""
    assert sign_in_with(client, {"type": "public-key"}).status_code == 400


@pytest.mark.parametrize("client_data", ["%%%", "bm90IGpzb24", "WzFd"])
def test_credential_with_unreadable_client_data(client: Client, client_data: str) -> None:
    """A clientDataJSON that isn't base64url JSON with a challenge is a 400."""
    options(client)
    credential = {**CREDENTIAL, "response": {"clientDataJSON": client_data}}
    assert answer(client, credential).status_code == 400


def test_passkey_options_are_rate_limited(client: Client) -> None:
    """Past the limit the options endpoint answers 429."""
    for _ in range(30):
        client.post(reverse("accounts:passkey_sign_in_options"))
    assert client.post(reverse("accounts:passkey_sign_in_options")).status_code == 429


@pytest.mark.parametrize(
    ("user_agent", "name"),
    [
        ("Mozilla/5.0 (iPad; CPU OS 18_0 like Mac OS X)", "Passkey on iPad"),
        ("Mozilla/5.0 (Linux; Android 15; Pixel 9)", "Passkey on Android"),
        ("Mozilla/5.0 (Macintosh; Intel Mac OS X 15_0)", "Passkey on Mac"),
        ("Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Passkey on Windows"),
        ("curl/8", "Passkey"),
    ],
)
def test_default_name(user_agent: str, name: str) -> None:
    """New passkeys are named after the device they were made on."""
    assert passkeys.default_name(user_agent) == name
