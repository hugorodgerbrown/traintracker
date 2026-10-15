"""Account models: an emailed sign-in request, a passkey, and a reviewer account.

There is no profile model: an account is a plain ``auth.User`` whose
username is its lowercased email address (``apps.accounts.sign_in``).
"""

from __future__ import annotations

import secrets
from typing import Any

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


# The same length as an emailed code (sign_in.CODE_DIGITS), so the code
# form takes it.
REVIEWER_CODE_DIGITS = 6


def new_reviewer_code() -> str:
    """Return a random code for a reviewer account."""
    return f"{secrets.randbelow(10**REVIEWER_CODE_DIGITS):0{REVIEWER_CODE_DIGITS}d}"


class ReviewerAccountQuerySet(models.QuerySet["ReviewerAccount"]):
    """Queries over reviewer accounts."""

    def for_email(self, email: str) -> ReviewerAccountQuerySet:
        """Return the reviewer account for ``email``, if there is one."""
        return self.filter(email=email.strip().lower())


class ReviewerAccount(BaseModel):
    """An address that signs in with a fixed code, for app directory reviewers.

    OpenAI's and Anthropic's reviews need a test account with no email or
    other verification step. This address is sent no email: on the code page
    it takes ``code``, which is made on the first save. The code is kept as
    it is, unlike an emailed one, so staff can read it here and paste it into
    the submission form. It is a shared test credential, not a person's
    secret; give a new one after each review (the admin action). Deleting
    the row turns the address back into an ordinary one.
    """

    email = models.EmailField(unique=True)
    code = models.CharField(max_length=REVIEWER_CODE_DIGITS, editable=False)

    objects = ReviewerAccountQuerySet.as_manager()

    class Meta(BaseModel.Meta):
        """Model metadata."""

        ordering = ["email"]

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Lowercase the address, and make a code if there isn't one."""
        self.email = self.email.strip().lower()
        if not self.code:
            self.code = new_reviewer_code()
        super().save(*args, **kwargs)

    def to_string(self) -> str:
        """Return the address."""
        return self.email
