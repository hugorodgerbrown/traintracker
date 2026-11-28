"""Start, claim and collect a TV pairing, called from the views.

The code is short because a person types it, so it is only good for ten
minutes, works once, and guesses are rate limited per address (views). The
poll token is long and random: it is what the TV holds, and what gets the
board.
"""

from __future__ import annotations

import hashlib
import logging
import re
import secrets
from dataclasses import dataclass
from datetime import timedelta

from django.utils import timezone
from django.utils.crypto import salted_hmac

from apps.pairing.models import TVPairing
from apps.public import board as tt

logger = logging.getLogger(__name__)

# No 0/O, 1/I/L: a code read off a TV across a room shouldn't be ambiguous.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 6
PAIRING_MINUTES = 10


@dataclass(frozen=True)
class StartedPairing:
    """A new pairing and the two secrets only the TV ever sees."""

    pairing: TVPairing
    code: str
    token: str


def _hash_token(token: str) -> str:
    """Hash a poll token for storage (it is random, so no salt is needed)."""
    return hashlib.sha256(token.encode()).hexdigest()


def _hash_code(code: str) -> str:
    """Hash a code with the server's secret, so the stored hash can't be brute-forced."""
    return salted_hmac("pairing.code", code, algorithm="sha256").hexdigest()


def normalise_code(text: str) -> str:
    """Return ``text`` as a bare upper-case code, without the spaces or dash people type."""
    return re.sub(r"[^A-Z0-9]", "", text.upper())


def format_code(code: str) -> str:
    """Return ``code`` split in two, as the TV shows it (``K7Q-4MD``)."""
    half = len(code) // 2
    return f"{code[:half]}-{code[half:]}"


def start() -> StartedPairing:
    """Start a pairing for a TV; return it with its code and poll token."""
    code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
    token = secrets.token_urlsafe(32)
    pairing = TVPairing.objects.create(
        token_hash=_hash_token(token),
        code_hash=_hash_code(code),
        expires_at=timezone.now() + timedelta(minutes=PAIRING_MINUTES),
    )
    return StartedPairing(pairing=pairing, code=code, token=token)


def claim(code: str, key: tt.BoardKey) -> TVPairing | None:
    """Give the live pairing with ``code`` the board ``key``; None if there is none.

    Two TVs could in theory draw the same code at once (one in ~900 million);
    the newest wins, and the other times out and starts again.
    """
    code = normalise_code(code)
    if len(code) != CODE_LENGTH:
        return None
    pairing = TVPairing.objects.live().filter(code_hash=_hash_code(code)).first()
    if pairing is None:
        return None
    now = timezone.now()
    # Conditional, so two people typing the same code can't both claim it.
    claimed = (
        TVPairing.objects.live()
        .filter(pk=pairing.pk)
        # A fresh expiry, so the TV has the full time to collect the board.
        .update(
            path=key.path,
            claimed_at=now,
            expires_at=now + timedelta(minutes=PAIRING_MINUTES),
            updated_at=now,
        )
    )
    if claimed != 1:
        return None
    logger.info("Claimed TV pairing %s for %s", pairing.uuid, key.path)
    pairing.refresh_from_db()
    return pairing


def find(token: str) -> TVPairing | None:
    """Return the pairing a poll token belongs to, whatever its state."""
    return TVPairing.objects.filter(token_hash=_hash_token(token)).first()


def collect(pairing: TVPairing) -> tt.BoardKey | None:
    """Hand over the claimed board; None if it's unclaimed or has expired.

    The TV may ask again until the pairing expires, so a reply lost on the
    way doesn't strand it; ``delivered_at`` records the first hand-over.
    """
    key = pairing.board()
    if pairing.claimed_at is None or key is None or pairing.is_expired():
        return None
    now = timezone.now()
    TVPairing.objects.filter(pk=pairing.pk, delivered_at__isnull=True).update(
        delivered_at=now, updated_at=now
    )
    return key
