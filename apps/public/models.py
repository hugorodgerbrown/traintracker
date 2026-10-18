"""Saved boards: the last board a signed-in person opened, and their favourites.

Signed out, the browser keeps them (static/js/saved_boards.js) and the server
keeps nothing. Signed in, they are kept here only, so they are the same on
every device. Why: docs/decisions/saved-boards-live-in-the-browser-or-the-account.md.
"""

from __future__ import annotations

from typing import ClassVar

from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser
from django.db import models

from apps.core.models import BaseModel
from apps.public import board as tt


class SavedBoardQuerySet(models.QuerySet["SavedBoard"]):
    """Queries over saved boards."""

    def for_user(self, user: AbstractBaseUser) -> SavedBoardQuerySet:
        """Return the boards ``user`` has saved."""
        return self.filter(user=user)

    def favourites(self) -> SavedBoardQuerySet:
        """Return the favourites, in the order they were added."""
        return self.filter(favourite=True).order_by("favourited_at", "id")

    def opened(self) -> SavedBoardQuerySet:
        """Return the boards that have been opened, the latest first."""
        return self.filter(opened_at__isnull=False).order_by("-opened_at", "-id")


class SavedBoard(BaseModel):
    """One board a person keeps: the last one they opened, a favourite, or both.

    ``path`` is the board's one address (/board/LST/COL), checked with
    ``board.from_path``. A row that is neither the last board opened nor a
    favourite is deleted, so an account holds at most ``MAX_FAVOURITES + 1``.
    """

    MAX_FAVOURITES: ClassVar[int] = 12

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="saved_boards"
    )
    path = models.CharField(max_length=64)
    favourite = models.BooleanField(default=False)
    favourited_at = models.DateTimeField(null=True, blank=True)
    opened_at = models.DateTimeField(null=True, blank=True)

    objects = SavedBoardQuerySet.as_manager()

    class Meta(BaseModel.Meta):
        """Model metadata."""

        ordering = ["-opened_at", "-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["user", "path"], name="saved_board_once_per_user")
        ]

    def to_string(self) -> str:
        """Return the board's short name, or its address if it no longer names a board."""
        key = tt.from_path(self.path)
        return key.name() if key else self.path
