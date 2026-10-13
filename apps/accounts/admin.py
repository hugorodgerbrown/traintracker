"""Admin for accounts: sign-in requests (read-only) and passkeys."""

from django.contrib import admin
from django.http import HttpRequest

from apps.accounts.models import Passkey, SignInRequest


@admin.register(SignInRequest)
class SignInRequestAdmin(admin.ModelAdmin):
    """Who asked to sign in and whether they did. The hashes stay hidden."""

    list_display = ("email", "created_at", "expires_at", "used_at", "code_attempts")
    search_fields = ("email",)
    fields = ("email", "next_url", "created_at", "expires_at", "used_at", "code_attempts")
    readonly_fields = fields

    def has_add_permission(self, request: HttpRequest) -> bool:
        """Requests are made by the sign-in form only."""
        return False


@admin.register(Passkey)
class PasskeyAdmin(admin.ModelAdmin):
    """Users' passkeys. Deleting one here stops it signing in."""

    list_display = ("name", "user", "backed_up", "created_at", "last_used_at")
    list_select_related = ("user",)
    search_fields = ("name", "user__email")
    fields = ("user", "name", "backed_up", "sign_count", "created_at", "last_used_at")
    readonly_fields = ("user", "backed_up", "sign_count", "created_at", "last_used_at")

    def has_add_permission(self, request: HttpRequest) -> bool:
        """Passkeys are made by the browser ceremony only."""
        return False
