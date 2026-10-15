"""System checks for accounts: the email key must be set.

Without ``ACCOUNTS_EMAIL_KEY`` no address can be keyed, so no one could
sign in; failing the checks stops ``migrate`` and so the deploy, rather
than the first sign-in.
"""

from typing import Any

from django.conf import settings
from django.core.checks import CheckMessage, Error, register


@register()
def email_key_is_set(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    """Fail when ``ACCOUNTS_EMAIL_KEY`` is empty."""
    if settings.ACCOUNTS_EMAIL_KEY:
        return []
    return [
        Error(
            "ACCOUNTS_EMAIL_KEY is not set.",
            hint="Set the ACCOUNTS_EMAIL_KEY environment variable (titan makes one).",
            id="accounts.E001",
        )
    ]
