"""Account models: an emailed sign-in request, and a passkey.

There is no profile model: an account is a plain ``auth.User`` whose
username is its lowercased email address (``apps.accounts.sign_in``).
"""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser
from django.db import models
from django.utils import timezone

from apps.core.models import BaseModel


class SignInRequestQuerySet(models.QuerySet["SignInRequest"]):
    """Queries over sign-in requests."""

    def live(self) -> SignInRequestQuerySet:
        """Return requests that are unused and unexpired."""
        return self.filter(used_at__isnull=True, expires_at__gt=timezone.now())

    def expired(self) -> SignInRequestQuerySet:
        """Return requests past their expiry, used or not: safe to delete."""
        return self.filter(expires_at__lte=timezone.now())

    def for_email(self, email: str) -> SignInRequestQuerySet:
        """Return the requests sent to ``email``."""
        return self.filter(email=email.strip().lower())


class SignInRequest(BaseModel):
    """One emailed sign-in: a link and a six-digit code, either usable once.

    Only hashes are stored, so a copy of the database signs no one in. The
    link works in any browser; the code only in the browser that asked for
    it (the session holds this row's uuid), which is what makes it safe to
    keep short. The code is there because an installed iPhone app has its
    own cookies: a link opened from Mail signs in Safari, not the app, so
    the app's user types the code instead.
    """

    email = models.EmailField(db_index=True)
    token_hash = models.CharField(max_length=64, unique=True)
    code_hash = models.CharField(max_length=64)
    next_url = models.CharField(max_length=2048, blank=True)
    expires_at = models.DateTimeField(db_index=True)
    used_at = models.DateTimeField(null=True, blank=True)
    code_attempts = models.PositiveSmallIntegerField(default=0)

    objects = SignInRequestQuerySet.as_manager()

    class Meta(BaseModel.Meta):
        """Model metadata."""

        ordering = ["-created_at"]

    def to_string(self) -> str:
        """Return who it was for and whether it was used."""
        state = "used" if self.used_at else "unused"
        return f"Sign-in for {self.email} ({state})"


class PasskeyQuerySet(models.QuerySet["Passkey"]):
    """Queries over passkeys."""

    def for_user(self, user: AbstractBaseUser) -> PasskeyQuerySet:
        """Return the passkeys ``user`` registered."""
        return self.filter(user=user)


class Passkey(BaseModel):
    """A WebAuthn credential a user added from the account page.

    ``credential_id`` (base64url) is how the browser names it at sign-in;
    ``public_key`` (COSE) verifies the signature. ``sign_count`` going
    backwards would mean a cloned authenticator; synced passkeys report 0.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="passkeys"
    )
    credential_id = models.CharField(max_length=1024, unique=True)
    public_key = models.BinaryField()
    sign_count = models.PositiveBigIntegerField(default=0)
    name = models.CharField(max_length=100)
    backed_up = models.BooleanField(
        default=False, help_text="Synced to the user's other devices (iCloud, Google…)."
    )
    last_used_at = models.DateTimeField(null=True, blank=True)

    objects = PasskeyQuerySet.as_manager()

    class Meta(BaseModel.Meta):
        """Model metadata."""

        ordering = ["-created_at"]

    def to_string(self) -> str:
        """Return the passkey's name."""
        return self.name
