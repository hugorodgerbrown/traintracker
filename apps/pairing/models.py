"""TV pairings: a short code the TV app shows, claimed with a board on a phone.

The TV app starts a pairing and shows its code; someone types it on their
phone and picks a board; the TV's next poll is answered with that board.
Only hashes of the code and the poll token are stored, so a copy of the
database links no TV to anything. Why: docs/decisions/a-tv-picks-its-board-from-a-phone.md.
"""

from __future__ import annotations

from django.db import models
from django.utils import timezone

from apps.core.models import BaseModel
from apps.public import board as tt


class TVPairingQuerySet(models.QuerySet["TVPairing"]):
    """Queries over pairings."""

    def live(self) -> TVPairingQuerySet:
        """Return pairings still waiting for their code: unclaimed and unexpired."""
        return self.filter(claimed_at__isnull=True, expires_at__gt=timezone.now())

    def expired(self) -> TVPairingQuerySet:
        """Return pairings past their expiry, claimed or not: safe to delete."""
        return self.filter(expires_at__lte=timezone.now())


class TVPairing(BaseModel):
    """One TV asking which board to show.

    ``claimed_at`` is when someone typed the code and ``path`` the board they
    picked (its one address, checked with ``board.from_path``);
    ``delivered_at`` is when the TV first collected it. The TV can collect it
    again until the pairing expires, then the row is purged.
    """

    token_hash = models.CharField(max_length=64, unique=True)
    code_hash = models.CharField(max_length=64, db_index=True)
    expires_at = models.DateTimeField(db_index=True)
    path = models.CharField(max_length=64, blank=True)
    claimed_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    objects = TVPairingQuerySet.as_manager()

    class Meta(BaseModel.Meta):
        """Model metadata."""

        ordering = ["-created_at", "-id"]

    def is_expired(self) -> bool:
        """Return True once the pairing is past its expiry."""
        return self.expires_at <= timezone.now()

    def board(self) -> tt.BoardKey | None:
        """Return the board it was claimed with, or None before that."""
        return tt.from_path(self.path) if self.path else None

    def to_string(self) -> str:
        """Return how far the pairing got."""
        if self.delivered_at:
            state = "delivered"
        elif self.claimed_at:
            state = "claimed"
        elif self.is_expired():
            state = "expired"
        else:
            state = "waiting"
        return f"TV pairing ({state})"
