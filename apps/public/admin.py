"""Admin for the public site: saved boards, read-only."""

from typing import Any

from django.contrib import admin
from django.http import HttpRequest

from apps.public.models import SavedBoard


@admin.register(SavedBoard)
class SavedBoardAdmin(admin.ModelAdmin):
    """Which boards accounts keep. People change them from the boards themselves."""

    list_display = ("path", "user", "favourite", "opened_at")
    list_filter = ("favourite",)
    search_fields = ("path",)
    readonly_fields = ("user", "path", "favourite", "favourited_at", "opened_at", "created_at")

    def has_add_permission(self, request: HttpRequest) -> bool:
        """Boards are saved from their pages only."""
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        """Read-only: staff may look, or delete, but not edit someone's boards."""
        return False
