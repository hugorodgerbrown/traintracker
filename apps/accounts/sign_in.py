"""Sign-in by email: issue a link and code, redeem either once, find the user.

There is no separate sign-up. The first redeemed link or code for an
address creates its account; until then nothing is stored about it beyond
the pending request. See docs/accounts.md.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from django.utils.crypto import salted_hmac

from apps.accounts.models import SignInRequest

logger = logging.getLogger(__name__)

CODE_DIGITS = 6


def normalise_email(email: str) -> str:
    """Return ``email`` as stored and looked up: trimmed and lowercased."""
    return email.strip().lower()


def _hash_token(token: str) -> str:
    """Hash a link token for storage (it is random, so no salt is needed)."""
    return hashlib.sha256(token.encode()).hexdigest()


def _hash_code(request_uuid: str, code: str) -> str:
    """Hash a code with the server's secret, so the stored hash can't be brute-forced."""
    return salted_hmac(
        "accounts.sign_in.code", f"{request_uuid}:{code}", algorithm="sha256"
    ).hexdigest()


def issue(email: str, next_url: str = "") -> tuple[SignInRequest, str, str]:
    """Create a sign-in request for ``email``; return it with its link token and code.

    Earlier unused requests for the same address stop working, so only the
    newest email in someone's inbox signs them in.
    """
    email = normalise_email(email)
    token = secrets.token_urlsafe(32)
    code = f"{secrets.randbelow(10**CODE_DIGITS):0{CODE_DIGITS}d}"
    now = timezone.now()
    with transaction.atomic():
        SignInRequest.objects.for_email(email).live().update(used_at=now)
        request = SignInRequest(
            email=email,
            token_hash=_hash_token(token),
            next_url=next_url[:2048],
            expires_at=now + timedelta(seconds=settings.SIGN_IN_MAX_AGE_SECONDS),
        )
        request.code_hash = _hash_code(str(request.uuid), code)
        request.save()
    return request, token, code


def _claim(request: SignInRequest) -> bool:
    """Mark ``request`` used, unless another redemption got there first."""
    claimed = (
        SignInRequest.objects.live()
        .filter(pk=request.pk)
        .update(used_at=timezone.now(), updated_at=timezone.now())
    )
    return claimed == 1


def find_by_token(token: str) -> SignInRequest | None:
    """Return the live request a link token belongs to, without using it up."""
    return SignInRequest.objects.live().filter(token_hash=_hash_token(token)).first()


def redeem_token(token: str) -> SignInRequest | None:
    """Use up a link token; return its request, or None if it is unknown, used or expired."""
    request = find_by_token(token)
    if request is None or not _claim(request):
        return None
    return request


def redeem_code(request_uuid: str, code: str) -> SignInRequest | None:
    """Use up a code typed in the browser that asked for it.

    Each wrong code counts; after ``SIGN_IN_MAX_CODE_ATTEMPTS`` the request
    stops accepting codes (its link still works).
    """
    request = SignInRequest.objects.live().filter(uuid=request_uuid).first()
    if request is None or request.code_attempts >= settings.SIGN_IN_MAX_CODE_ATTEMPTS:
        return None
    expected = _hash_code(str(request.uuid), code.strip())
    if not hmac.compare_digest(expected, request.code_hash):
        SignInRequest.objects.filter(pk=request.pk).update(code_attempts=F("code_attempts") + 1)
        return None
    if not _claim(request):
        return None
    return request


def user_for(request: SignInRequest) -> Any | None:
    """Return the account for a redeemed request, creating it on first sign-in.

    Returns None for a deactivated account: it can't be signed back in by
    email. Existing accounts are found by username (the email, for accounts
    made here) or by email (for ones made with ``createsuperuser``).
    """
    user_model = get_user_model()
    email = request.email
    user = (
        user_model.objects.filter(username=email).first()
        or user_model.objects.filter(email__iexact=email).order_by("pk").first()
    )
    if user is None:
        user = user_model(username=email, email=email)
        user.set_unusable_password()
        user.save()
        logger.info("accounts.created user=%s", user.pk)
    if not user.is_active:
        return None
    return user
