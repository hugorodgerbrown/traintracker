"""Delete accounts nobody has used for IDLE_DAYS, as the privacy policy promises.

An account is in use while its owner signs in or one of its connected apps
gets a token (a refresh counts). Staff accounts are never purged. Read-only
by default; ``--commit`` deletes, which takes the account's passkeys and
tokens with it.
"""

from datetime import timedelta
from typing import Any

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandParser
from django.db.models import Max, QuerySet
from django.db.models.functions import Coalesce, Greatest
from django.utils import timezone

IDLE_DAYS = 180


def idle_accounts() -> QuerySet[Any]:
    """Return the non-staff accounts with no sign-in and no token for IDLE_DAYS."""
    cutoff = timezone.now() - timedelta(days=IDLE_DAYS)
    epoch = timezone.now() - timedelta(days=365 * 100)
    return (
        get_user_model()
        .objects.filter(is_staff=False, is_superuser=False)
        .annotate(
            last_used=Greatest(
                Coalesce("last_login", "date_joined"),
                Coalesce(Max("oauth2_provider_accesstoken__created"), epoch),
            )
        )
        .filter(last_used__lt=cutoff)
    )


class Command(BaseCommand):
    """Purge idle accounts."""

    help = f"Delete accounts unused for {IDLE_DAYS} days (dry run unless --commit)."

    def add_arguments(self, parser: CommandParser) -> None:
        """Add the --commit flag."""
        parser.add_argument("--commit", action="store_true", help="Actually delete them.")

    def handle(self, *args: Any, **options: Any) -> None:
        """Count, and with --commit delete, the idle accounts."""
        idle = get_user_model().objects.filter(pk__in=idle_accounts().values("pk"))
        if not options["commit"]:
            self.stdout.write(f"{idle.count()} idle accounts (dry run; pass --commit).")
            return
        count = idle.count()
        idle.delete()
        if options["verbosity"] > 0:
            self.stdout.write(f"Deleted {count} idle accounts.")
