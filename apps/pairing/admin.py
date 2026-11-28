"""Admin for TV pairings: read-only, for seeing whether a TV got its board."""

from django.contrib import admin
from django.http import HttpRequest

from apps.pairing.models import TVPairing


@admin.register(TVPairing)
class TVPairingAdmin(admin.ModelAdmin):
    """Pairings, newest first. Codes and tokens are hashes, so they aren't shown."""

    list_display = ("to_string", "path", "created_at", "claimed_at", "delivered_at")
    fields = ("uuid", "path", "created_at", "expires_at", "claimed_at", "delivered_at")
    readonly_fields = fields

    def has_add_permission(self, request: HttpRequest) -> bool:
        """Pairings are only started by a TV."""
        return False
