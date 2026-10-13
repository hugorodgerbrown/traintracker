"""Passkeys (WebAuthn), wrapping py_webauthn.

Two ceremonies, each a pair of requests: the server issues options with a
fresh challenge (kept in the session), the browser signs it, and the
server verifies the answer against the same challenge, which is then
dropped so it can't be replayed.

The sign-in page can run two ceremonies at once (the email field's
autofill and the passkey button), so the session keeps the last few
sign-in challenges and each answer is checked against the one it signed.

Lifted from Snowdesk's ``apps/accounts/services/passkey.py``, with user
verification required: a passkey alone signs someone in, so it has to be
the person (Face ID, fingerprint, PIN) and not just the device.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, cast

import webauthn
from django.conf import settings
from django.contrib.sessions.backends.base import SessionBase
from django.utils import timezone
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url, options_to_json
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from apps.accounts.models import Passkey

logger = logging.getLogger(__name__)

_REGISTER_CHALLENGE = "passkeys.register_challenge"
_SIGN_IN_CHALLENGES = "passkeys.sign_in_challenges"
# Outstanding sign-in challenges kept per session; older ones lapse.
MAX_SIGN_IN_CHALLENGES = 5


class PasskeyError(Exception):
    """The browser's answer didn't verify, or there was no challenge to check it against."""


class UnknownPasskeyError(PasskeyError):
    """The browser offered a passkey this site doesn't know (deleted here, kept on the device)."""

    def __init__(self, credential_id: str) -> None:
        self.credential_id = credential_id
        super().__init__("Unknown passkey.")


def _user_handle(user: Any) -> bytes:
    """The WebAuthn user id: stable and not personal data (the spec forbids an email)."""
    return hashlib.sha256(str(user.pk).encode()).digest()


def _options_dict(options: Any) -> dict[str, Any]:
    """Return py_webauthn options as the JSON-ready dict the browser expects."""
    return cast(dict[str, Any], json.loads(options_to_json(options)))


def registration_options(user: Any, session: SessionBase) -> dict[str, Any]:
    """Return options for ``navigator.credentials.create()`` for ``user``.

    Lists the user's existing passkeys so a device that already has one
    says so rather than making a second.
    """
    options = webauthn.generate_registration_options(
        rp_id=settings.WEBAUTHN_RP_ID,
        rp_name=settings.WEBAUTHN_RP_NAME,
        user_id=_user_handle(user),
        user_name=user.email or user.get_username(),
        user_display_name=user.email or user.get_username(),
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(p.credential_id))
            for p in Passkey.objects.for_user(user)
        ],
    )
    session[_REGISTER_CHALLENGE] = bytes_to_base64url(options.challenge)
    return _options_dict(options)


def register(credential_json: str, session: SessionBase, user: Any, name: str) -> Passkey:
    """Verify the browser's ``create()`` answer and save the new passkey."""
    challenge = session.pop(_REGISTER_CHALLENGE, None)
    if not challenge:
        raise PasskeyError("No registration challenge in the session.")
    try:
        verified = webauthn.verify_registration_response(
            credential=credential_json,
            expected_challenge=base64url_to_bytes(challenge),
            expected_rp_id=settings.WEBAUTHN_RP_ID,
            expected_origin=settings.WEBAUTHN_ORIGINS,
            require_user_verification=True,
        )
    except Exception as exc:
        raise PasskeyError("Registration didn't verify.") from exc

    credential_id = bytes_to_base64url(verified.credential_id)
    if Passkey.objects.filter(credential_id=credential_id).exists():
        raise PasskeyError("That passkey is already registered.")
    passkey = Passkey.objects.create(
        user=user,
        credential_id=credential_id,
        public_key=verified.credential_public_key,
        sign_count=verified.sign_count,
        backed_up=verified.credential_backed_up,
        name=name[:100],
    )
    logger.info("passkeys.registered user=%s passkey=%s", user.pk, passkey.uuid)
    return passkey


def sign_in_options(session: SessionBase) -> dict[str, Any]:
    """Return options for ``navigator.credentials.get()``.

    No ``allowCredentials``: the browser offers every passkey it holds for
    this site, so sign-in needs no email address first.
    """
    options = webauthn.generate_authentication_options(
        rp_id=settings.WEBAUTHN_RP_ID,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    challenges = [*session.get(_SIGN_IN_CHALLENGES, []), bytes_to_base64url(options.challenge)]
    session[_SIGN_IN_CHALLENGES] = challenges[-MAX_SIGN_IN_CHALLENGES:]
    return _options_dict(options)


def _signed_challenge(credential: dict[str, Any]) -> str:
    """Return the challenge the browser signed, from the answer's clientDataJSON."""
    client_data = json.loads(base64url_to_bytes(credential["response"]["clientDataJSON"]))
    return str(client_data["challenge"])


def authenticate(credential_json: str, session: SessionBase) -> Any:
    """Verify the browser's ``get()`` answer and return the passkey's user."""
    try:
        credential = json.loads(credential_json)
        credential_id = str(credential["id"])
        challenge = _signed_challenge(credential)
    except (ValueError, KeyError, TypeError) as exc:
        raise PasskeyError("Not a WebAuthn credential.") from exc
    challenges = session.get(_SIGN_IN_CHALLENGES, [])
    if challenge not in challenges:
        raise PasskeyError("No matching sign-in challenge in the session.")
    # Used once, whether or not it verifies.
    session[_SIGN_IN_CHALLENGES] = [c for c in challenges if c != challenge]

    passkey = Passkey.objects.select_related("user").filter(credential_id=credential_id).first()
    if passkey is None:
        raise UnknownPasskeyError(credential_id)
    try:
        verified = webauthn.verify_authentication_response(
            credential=credential_json,
            expected_challenge=base64url_to_bytes(challenge),
            expected_rp_id=settings.WEBAUTHN_RP_ID,
            expected_origin=settings.WEBAUTHN_ORIGINS,
            credential_public_key=bytes(passkey.public_key),
            credential_current_sign_count=passkey.sign_count,
            require_user_verification=True,
        )
    except Exception as exc:
        raise PasskeyError("Sign-in didn't verify.") from exc

    passkey.sign_count = verified.new_sign_count
    passkey.backed_up = verified.credential_backed_up
    passkey.last_used_at = timezone.now()
    passkey.save(update_fields=["sign_count", "backed_up", "last_used_at", "updated_at"])
    return passkey.user


_DEVICE_NAMES = (
    ("iPhone", "iPhone"),
    ("iPad", "iPad"),
    ("Android", "Android"),
    ("CrOS", "Chromebook"),
    ("Windows", "Windows"),
    ("Macintosh", "Mac"),
    ("Linux", "Linux"),
)


def default_name(user_agent: str) -> str:
    """Name a new passkey after the device it was made on ("Passkey on iPhone")."""
    for marker, device in _DEVICE_NAMES:
        if marker in user_agent:
            return f"Passkey on {device}"
    return "Passkey"
