"""Admin for accounts: sign-in requests (read-only), passkeys and reviewer accounts."""

from django.contrib import admin, messages
from django.db.models import QuerySet
from django.http import HttpRequest

from apps.accounts.models import Passkey, ReviewerAccount, SignInRequest, new_reviewer_code


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


@admin.register(ReviewerAccount)
class ReviewerAccountAdmin(admin.ModelAdmin):
    """Addresses app directory reviewers sign in with, and their fixed codes."""

    list_display = ("email", "code", "created_at", "updated_at")
    search_fields = ("email",)
    fields = ("email", "code", "created_at", "updated_at")
    readonly_fields = ("code", "created_at", "updated_at")
    actions = ("give_new_code",)

    @admin.action(description="Give the selected accounts a new code")
    def give_new_code(self, request: HttpRequest, queryset: QuerySet[ReviewerAccount]) -> None:
        """Replace each selected account's code, as after a review."""
        count = 0
        for account in queryset:
            account.code = new_reviewer_code()
            account.save(update_fields=["code", "updated_at"])
            count += 1
        noun = "account" if count == 1 else "accounts"
        self.message_user(request, f"Gave {count} {noun} a new code.", messages.SUCCESS)
