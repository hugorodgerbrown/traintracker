"""Admin for accounts: users, sign-in requests (read-only), passkeys and reviewer accounts."""

from typing import Any

from django.contrib import admin, messages
from django.contrib.auth import forms as auth_forms
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.db.models import QuerySet
from django.http import HttpRequest

from apps.accounts.models import Passkey, ReviewerAccount, SignInRequest, User, username_for


class UserChangeForm(auth_forms.UserChangeForm):
    """Django's user form, for this project's user model."""

    class Meta(auth_forms.UserChangeForm.Meta):
        """Form metadata."""

        model = User
        fields = "__all__"


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    """Accounts, listed by masked address. Search takes a whole address too.

    The username is the address's key unless addresses are kept, so a full
    address typed into search is turned into its username and matched.
    """

    form = UserChangeForm
    list_display = ("masked_email", "is_staff", "is_active", "date_joined", "last_login")
    search_fields = ("masked_email", "username")
    ordering = ("-date_joined",)
    readonly_fields = ("username", "masked_email", "date_joined", "last_login")
    fieldsets = (
        (None, {"fields": ("masked_email", "username", "email")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups")}),
        ("Dates", {"fields": ("date_joined", "last_login")}),
    )

    def get_search_results(
        self, request: HttpRequest, queryset: QuerySet[Any], search_term: str
    ) -> tuple[QuerySet[Any], bool]:
        """Match the fields as usual, and an address by the username it gives."""
        results, may_have_duplicates = super().get_search_results(request, queryset, search_term)
        if "@" in search_term:
            results |= queryset.filter(username=username_for(search_term))
        return results, may_have_duplicates

    def has_add_permission(self, request: HttpRequest) -> bool:
        """Accounts are made by signing in (or ``createsuperuser``) only."""
        return False


@admin.register(SignInRequest)
class SignInRequestAdmin(admin.ModelAdmin):
    """Who asked to sign in, masked, and whether they did. The hashes stay hidden."""

    list_display = ("masked_email", "created_at", "expires_at", "used_at", "code_attempts")
    search_fields = ("masked_email",)
    fields = ("masked_email", "next_url", "created_at", "expires_at", "used_at", "code_attempts")
    readonly_fields = fields

    def has_add_permission(self, request: HttpRequest) -> bool:
        """Requests are made by the sign-in form only."""
        return False


@admin.register(Passkey)
class PasskeyAdmin(admin.ModelAdmin):
    """Users' passkeys. Deleting one here stops it signing in."""

    list_display = ("name", "user", "backed_up", "created_at", "last_used_at")
    list_select_related = ("user",)
    search_fields = ("name", "user__masked_email")
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
            account.give_new_code()
            count += 1
        noun = "account" if count == 1 else "accounts"
        self.message_user(request, f"Gave {count} {noun} a new code.", messages.SUCCESS)

    def delete_queryset(self, request: HttpRequest, queryset: QuerySet[ReviewerAccount]) -> None:
        """Delete one by one, so each ends the sign-ins it had started."""
        for account in queryset:
            account.delete()
