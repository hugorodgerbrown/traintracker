"""App config for apps.accounts."""

from django.apps import AppConfig


class AccountsConfig(AppConfig):
    """Sign-in by emailed link or code, passkeys, and the account page."""

    name = "apps.accounts"
    label = "accounts"

    def ready(self) -> None:
        """Register the system checks."""
        from apps.accounts import checks  # noqa: F401
