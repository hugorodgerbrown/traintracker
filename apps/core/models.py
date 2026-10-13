"""Core models: the abstract BaseModel and the idempotency record.

Every concrete model in the project inherits ``BaseModel`` and ships the
full kit described in CLAUDE.md (admin, ``to_string()``, ordering, a
custom queryset, a factory and tests).
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta

from django.db import IntegrityError, models, transaction
from django.http import HttpResponse
from django.utils import timezone

logger = logging.getLogger(__name__)

# How many times reserve() retries when a competing request keeps
# releasing the key between our INSERT and our read.
_RESERVE_ATTEMPTS = 3


class BaseModel(models.Model):
    """Abstract base: big-int primary key, a public uuid and timestamps.

    Use ``uuid`` in URLs and anything a client sees; the integer ``id`` stays
    internal.
    """

    id = models.BigAutoField(primary_key=True)
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """Model metadata shared by every concrete model."""

        abstract = True
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.to_string()

    def to_string(self) -> str:
        """Return a short human-readable description; override per model."""
        return f"{type(self).__name__}({self.pk})"


class IdempotencyRecordQuerySet(models.QuerySet["IdempotencyRecord"]):
    """Queries over idempotency records."""

    def live(self) -> IdempotencyRecordQuerySet:
        """Return records whose retention window has not expired."""
        return self.filter(expires_at__gt=timezone.now())

    def expired(self) -> IdempotencyRecordQuerySet:
        """Return records past their retention window, safe to delete."""
        return self.filter(expires_at__lte=timezone.now())


class IdempotencyRecordManager(models.Manager["IdempotencyRecord"]):
    """Manager carrying the atomic ``reserve()`` primitive."""

    def get_queryset(self) -> IdempotencyRecordQuerySet:
        """Return the custom queryset."""
        return IdempotencyRecordQuerySet(self.model, using=self._db)

    def expired(self) -> IdempotencyRecordQuerySet:
        """Shortcut for ``get_queryset().expired()``."""
        return self.get_queryset().expired()

    def reserve(
        self,
        *,
        key: str,
        method: str,
        path: str,
        principal: str,
        body_hash: str,
        ttl: timedelta,
    ) -> tuple[IdempotencyRecord | None, bool]:
        """Claim ``key`` before the view runs, or report who holds it.

        Claiming first is what makes the guarantee at-most-once under
        concurrency: two requests with the same key can't both reach the
        view. A fresh key is an INSERT; an expired one is taken over by a
        conditional UPDATE that only one claimant can win.

        Returns:
            ``(record, claimed)``. When ``claimed`` is True the caller owns
            the reservation and must ``complete()`` or ``release()`` it.
            Otherwise ``record`` is the row someone else holds, or None if
            the claim could not be settled (treat that as a failed claim).
        """
        fingerprint = {
            "method": method,
            "path": path[:2048],
            "principal": principal,
            "body_hash": body_hash,
        }
        for _ in range(_RESERVE_ATTEMPTS):
            now = timezone.now()
            try:
                # A savepoint, so a unique-key collision doesn't poison an
                # outer transaction.
                with transaction.atomic(using=self._db):
                    return (
                        self.create(key=key, expires_at=now + ttl, **fingerprint),
                        True,
                    )
            except IntegrityError:
                pass

            claimed = self.filter(key=key, expires_at__lte=now).update(
                status=IdempotencyRecord.Status.RESERVED,
                response_status=None,
                response_body=b"",
                response_content_type="",
                expires_at=now + ttl,
                updated_at=now,
                **fingerprint,
            )
            try:
                return self.get(key=key), bool(claimed)
            except self.model.DoesNotExist:
                # The holder released it between our INSERT and this read.
                continue

        logger.warning("idempotency.reserve_exhausted attempts=%d", _RESERVE_ATTEMPTS)
        return None, False


class IdempotencyRecord(BaseModel):
    """One response per ``Idempotency-Key``, replayed for a repeated request.

    Written by ``apps.core.idempotency.IdempotencyMiddleware`` in two steps:
    a RESERVED row before the view runs, promoted to COMPLETED with the
    response afterwards. The offline outbox resends a write with the same
    key until it gets an answer, so a write that reached the server but
    whose reply was lost is never applied twice.
    """

    class Status(models.TextChoices):
        """Lifecycle of a reservation."""

        RESERVED = "reserved", "Reserved"
        COMPLETED = "completed", "Completed"

    key = models.CharField(max_length=128, unique=True)
    method = models.CharField(max_length=16)
    path = models.CharField(max_length=2048)
    principal = models.CharField(
        max_length=64, blank=True, help_text="The user's pk, or blank when signed out."
    )
    body_hash = models.CharField(max_length=64, help_text="sha256 of the raw request body.")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.RESERVED)
    response_status = models.PositiveSmallIntegerField(null=True, blank=True)
    response_body = models.BinaryField(default=b"")
    response_content_type = models.CharField(max_length=255, blank=True)
    expires_at = models.DateTimeField(db_index=True)

    objects = IdempotencyRecordManager()

    class Meta(BaseModel.Meta):
        """Model metadata."""

        ordering = ["-created_at"]

    def to_string(self) -> str:
        """Return the method, path and a redacted key."""
        return f"{self.method} {self.path} key={self.key[:8]}… {self.status}"

    def is_completed(self) -> bool:
        """Return True when a response has been captured and can be replayed."""
        return self.status == self.Status.COMPLETED

    def matches(self, *, method: str, path: str, principal: str, body_hash: str) -> bool:
        """Return True when a request is a true repeat of the one that claimed the key.

        A key reused for a different path, body or user must not be handed
        someone else's response.
        """
        return (self.method, self.path, self.principal, self.body_hash) == (
            method,
            path[:2048],
            principal,
            body_hash,
        )

    def complete(self, *, response: HttpResponse, ttl: timedelta) -> None:
        """Store ``response`` so a repeat of this request can be answered with it."""
        self.status = self.Status.COMPLETED
        self.response_status = response.status_code
        self.response_body = bytes(response.content)
        self.response_content_type = str(response.get("Content-Type", ""))[:255]
        self.expires_at = timezone.now() + ttl
        self.save(
            update_fields=[
                "status",
                "response_status",
                "response_body",
                "response_content_type",
                "expires_at",
                "updated_at",
            ]
        )

    def release(self) -> None:
        """Drop an unfinished reservation so the key can be claimed again at once."""
        type(self).objects.filter(pk=self.pk, status=self.Status.RESERVED).delete()

    def build_response(self) -> HttpResponse:
        """Rebuild the stored response, marked as a replay."""
        response = HttpResponse(
            bytes(self.response_body),
            status=self.response_status or 200,
            content_type=self.response_content_type or None,
        )
        response["Idempotent-Replayed"] = "true"
        return response
