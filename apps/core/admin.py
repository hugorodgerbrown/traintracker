"""Admin for core models."""

from django.contrib import admin
from django.http import HttpRequest

from apps.core.models import IdempotencyRecord


@admin.register(IdempotencyRecord)
class IdempotencyRecordAdmin(admin.ModelAdmin):
    """Read-only view of stored idempotent responses, for debugging replays."""

    list_display = ("method", "path", "status", "response_status", "principal", "expires_at")
    list_filter = ("status", "method")
    search_fields = ("key", "path")
    readonly_fields = [f.name for f in IdempotencyRecord._meta.fields]

    def has_add_permission(self, request: HttpRequest) -> bool:
        """Records are written by the middleware only."""
        return False
