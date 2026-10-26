"""Sign-in by email: issue a link and code, redeem either once, find the user.

There is no separate sign-up. The first redeemed link or code for an
address creates its account; until then nothing is stored about it beyond
the pending request. The address itself is stored nowhere unless
``ACCOUNTS_KEEP_EMAIL`` is on: the request and the account hold its key and
masked form (``apps.accounts.models``). See docs/accounts.md.
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
from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.utils import timezone
from django.utils.crypto import salted_hmac

from apps.accounts.models import (
    ReviewerAccount,
    SignInRequest,
    email_key,
    kept_email,
    mask_email,
    normalise_email,
    username_for,
)

logger = logging.getLogger(__name__)

CODE_DIGITS = 6
# Tries at storing a request when others for the address land at once.
ISSUE_ATTEMPTS = 5


def _hash_token(token: str) -> str:
    """Hash a link token for storage (it is random, so no salt is needed)."""
    return hashlib.sha256(token.encode()).hexdigest()


def _hash_code(request_uuid: str, code: str) -> str:
    """Hash a code with the server's secret, so the stored hash can't be brute-forced."""
    return salted_hmac(
        "accounts.sign_in.code", f"{request_uuid}:{code}", algorithm="sha256"
    ).hexdigest()


def reviewer_code(email: str) -> str | None:
    """Return the fixed code if ``email`` is a reviewer account's, else None.

    Reviewers have no inbox to read, so their address is sent no email and
    signs in with the code staff set up in the admin (see docs/accounts.md).
    """
    return ReviewerAccount.objects.for_email(email).values_list("code", flat=True).first()


def issue(email: str, next_url: str = "") -> tuple[SignInRequest, str, str]:
    """Create a sign-in request for ``email``; return it with its link token and code.

    Earlier unused requests for the same address stop working, so only the
    newest email in someone's inbox signs them in. A reviewer account's
    address gets its fixed code (``reviewer_code``).
    """
    email = normalise_email(email)
    token = secrets.token_urlsafe(32)
    code = reviewer_code(email) or f"{secrets.randbelow(10**CODE_DIGITS):0{CODE_DIGITS}d}"
    # The database allows one unused request per address. Requests at the
    # same moment all find nothing to retire; each one that collides tries
    # again and retires the winner, so the newest still wins.
    for attempt in range(ISSUE_ATTEMPTS):
        try:
            request = _replace_request(email, token, code, next_url)
            break
        except IntegrityError:
            if attempt == ISSUE_ATTEMPTS - 1:
                raise
    return request, token, code


def _replace_request(email: str, token: str, code: str, next_url: str) -> SignInRequest:
    """Retire ``email``'s unused requests and store a new one, in one transaction."""
    now = timezone.now()
    with transaction.atomic():
        SignInRequest.objects.for_email(email).unused().update(used_at=now)
        request = SignInRequest(
            email_key=email_key(email),
            masked_email=mask_email(email),
            email=kept_email(email),
            token_hash=_hash_token(token),
            next_url=next_url[:2048],
            expires_at=now + timedelta(seconds=settings.SIGN_IN_MAX_AGE_SECONDS),
        )
        request.code_hash = _hash_code(str(request.uuid), code)
        request.save()
    return request


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


def _username(request: SignInRequest) -> str:
    """Return the username the request's account has: its key, or the kept address."""
    return request.email or request.email_key


def _adopt_account(request: SignInRequest) -> Any | None:
    """Return an account stored another way for the request's address, restored now.

    Two kinds: one ``createsuperuser`` made before it keyed accounts, which
    stores the address it was given; and one made before ``ACCOUNTS_KEEP_EMAIL`` changed, whose
    username is the other form (the key, or the address). Either is given
    the request's username, masked address and email (blank unless
    addresses are kept). Where addresses aren't kept, only accounts made
    with one still have it, so there are few to look through.
    """
    user_model = get_user_model()
    if request.email:
        candidates = user_model.objects.filter(
            Q(email__iexact=request.email) | Q(username=request.email_key)
        )
    else:
        candidates = user_model.objects.exclude(email="")
    user = next(
        (
            u
            for u in candidates.order_by("pk")
            if u.username == request.email_key or username_for(u.email) == _username(request)
        ),
        None,
    )
    if user is not None:
        user.username = _username(request)
        user.masked_email = request.masked_email
        user.email = request.email
        user.save(update_fields=["username", "masked_email", "email"])
        logger.info("accounts.restored user=%s", user.pk)
    return user


def adopt_account(email: str) -> Any | None:
    """Return the account ``email`` had in an older form, restored now, if there is one.

    ``createsuperuser`` asks before it makes an account, so it promotes the
    account sign-in would adopt rather than making a second that strands it.
    """
    request = SignInRequest(
        email_key=email_key(email), masked_email=mask_email(email), email=kept_email(email)
    )
    return _adopt_account(request)


def user_for(request: SignInRequest) -> Any | None:
    """Return the account for a redeemed request, creating it on first sign-in.

    Returns None for a deactivated account: it can't be signed back in by
    email. Accounts are found by username (``username_for``), or, the first
    time after ``createsuperuser`` or a change to ``ACCOUNTS_KEEP_EMAIL``,
    as ``_adopt_account`` says.
    """
    user_model = get_user_model()
    user = user_model.objects.filter(username=_username(request)).first()
    if user is None:
        user = _adopt_account(request)
    if user is None:
        user = user_model(
            username=_username(request),
            email=request.email,
            masked_email=request.masked_email,
        )
        user.set_unusable_password()
        user.save()
        logger.info("accounts.created user=%s", user.pk)
    if not user.is_active:
        return None
    return user
