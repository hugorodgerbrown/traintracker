"""Account models: the user, an emailed sign-in request, a passkey, a reviewer account.

By default no email address is stored. An account's username is
``email_key(address)``, a keyed hash that finds the same account each time
someone signs in with that address, and ``masked_email`` (``h•••@example.com``)
is kept so the person can see which account they are in. An app that has to
email people later sets ``ACCOUNTS_KEEP_EMAIL = True``, and the address is
the username and the email as well. See
docs/decisions/accounts-keep-a-key-not-an-email-address.md.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Any, ClassVar

from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import AbstractUser, UserManager
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.utils import timezone

from apps.core.models import BaseModel


def normalise_email(email: str) -> str:
    """Return ``email`` as it is keyed, stored and looked up: trimmed and lowercased."""
    return email.strip().lower()


def email_key(email: str) -> str:
    """Return the HMAC-SHA256 of the normalised ``email``, keyed with ``ACCOUNTS_EMAIL_KEY``.

    Its own key, not ``SECRET_KEY``, so rotating that (sessions, signed
    links) doesn't lose every account. Changing or losing this one does:
    every address then hashes to a key no account has.
    """
    secret = settings.ACCOUNTS_EMAIL_KEY
    if not secret:
        raise ImproperlyConfigured("ACCOUNTS_EMAIL_KEY is not set.")
    return hmac.new(secret.encode(), normalise_email(email).encode(), hashlib.sha256).hexdigest()


def mask_email(email: str) -> str:
    """Return the address's first letter and domain: ``h•••@example.com``."""
    name, _, domain = normalise_email(email).partition("@")
    return f"{name[:1]}•••@{domain}"


def username_for(email: str) -> str:
    """Return the username an account for ``email`` has: its key, or the address if kept."""
    return normalise_email(email) if settings.ACCOUNTS_KEEP_EMAIL else email_key(email)


def kept_email(email: str) -> str:
    """Return ``email`` as stored: normalised if addresses are kept, else blank."""
    return normalise_email(email) if settings.ACCOUNTS_KEEP_EMAIL else ""


class UserQuerySet(models.QuerySet["User"]):
    """Queries over accounts."""

    def for_email(self, email: str) -> UserQuerySet:
        """Return the account signing in with ``email`` reaches, if there is one."""
        return self.filter(username=username_for(email))

    def staff(self) -> UserQuerySet:
        """Return the accounts that can use the admin."""
        return self.filter(is_staff=True)


class AccountManager(UserManager.from_queryset(UserQuerySet)):  # type: ignore[misc]
    """Django's user manager (``create_user``, ``create_superuser``) with ``UserQuerySet``."""


class User(BaseModel, AbstractUser):
    """An account. Sign-in makes one; it has no usable password.

    ``username`` is ``email_key(address)`` and ``email`` is blank, unless
    ``ACCOUNTS_KEEP_EMAIL`` is on (``username_for``, ``kept_email``).
    ``masked_email`` is set either way, so pages show the same thing.
    """

    masked_email = models.CharField(max_length=254, blank=True)

    objects: ClassVar[AccountManager] = AccountManager()

    class Meta(AbstractUser.Meta, BaseModel.Meta):
        """Model metadata."""

        abstract = False
        swappable = "AUTH_USER_MODEL"
        ordering = ["-date_joined"]

    def to_string(self) -> str:
        """Return the masked address, or the username for an account made without one."""
        return self.masked_email or self.get_username()


class SignInRequestQuerySet(models.QuerySet["SignInRequest"]):
    """Queries over sign-in requests."""

    def live(self) -> SignInRequestQuerySet:
        """Return requests that are unused and unexpired."""
        return self.filter(used_at__isnull=True, expires_at__gt=timezone.now())

    def expired(self) -> SignInRequestQuerySet:
        """Return requests past their expiry, used or not: safe to delete."""
        return self.filter(expires_at__lte=timezone.now())

    def for_email(self, email: str) -> SignInRequestQuerySet:
        """Return the requests sent to ``email`` (matched by its key)."""
        return self.filter(email_key=email_key(email))


class SignInRequest(BaseModel):
    """One emailed sign-in: a link and a six-digit code, either usable once.

    Only hashes are stored, so a copy of the database signs no one in. The
    link works in any browser; the code only in the browser that asked for
    it (the session holds this row's uuid), which is what makes it safe to
    keep short. The code is there because an installed iPhone app has its
    own cookies: a link opened from Mail signs in Safari, not the app, so
    the app's user types the code instead.

    The address itself is kept only with ``ACCOUNTS_KEEP_EMAIL``; otherwise
    the request holds its key and masked form, which is what the account
    made from it needs.
    """

    email_key = models.CharField(max_length=64, db_index=True)
    masked_email = models.CharField(max_length=254)
    email = models.EmailField(blank=True)
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
        return f"Sign-in for {self.masked_email} ({state})"


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
        return self.filter(email=normalise_email(email))


class ReviewerAccount(BaseModel):
    """An address that signs in with a fixed code, for app directory reviewers.

    OpenAI's and Anthropic's reviews need a test account with no email or
    other verification step. This address is sent no email: on the code page
    it takes ``code``, which is made on the first save. The code is kept as
    it is, unlike an emailed one, so staff can read it here and paste it into
    the submission form. It is a shared test credential, not a person's
    secret; give a new one after each review (the admin action). Deleting
    the row turns the address back into an ordinary one.

    The address is kept as it is too, even where accounts keep none: it has
    no mailbox or person behind it, and staff read it here.
    """

    email = models.EmailField(unique=True)
    code = models.CharField(max_length=REVIEWER_CODE_DIGITS, editable=False)

    objects = ReviewerAccountQuerySet.as_manager()

    class Meta(BaseModel.Meta):
        """Model metadata."""

        ordering = ["email"]

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Normalise the address, and make a code if there isn't one.

        Changing the address ends the old one's pending sign-ins, which hold
        this code: it is no longer a reviewer address.
        """
        self.email = normalise_email(self.email)
        if not self.code:
            self.code = new_reviewer_code()
        update_fields = kwargs.get("update_fields")
        email_saved = update_fields is None or "email" in update_fields
        previous = (
            ReviewerAccount.objects.filter(pk=self.pk).values_list("email", flat=True).first()
            if self.pk and email_saved
            else None
        )
        super().save(*args, **kwargs)
        if previous and previous != self.email:
            SignInRequest.objects.for_email(previous).live().update(used_at=timezone.now())

    def give_new_code(self) -> None:
        """Replace the code, and end sign-ins already started with the old one."""
        self.code = new_reviewer_code()
        self.save(update_fields=["code", "updated_at"])
        self.end_pending_sign_ins()

    def end_pending_sign_ins(self) -> None:
        """Stop this address's unused sign-in requests working.

        A request holds the code it was issued with, so without this an old
        code would keep working in a browser that asked for it until the
        request expired.
        """
        SignInRequest.objects.for_email(self.email).live().update(used_at=timezone.now())

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        """Delete the account, ending any sign-in it had started."""
        self.end_pending_sign_ins()
        return super().delete(*args, **kwargs)

    def to_string(self) -> str:
        """Return the address."""
        return self.email
