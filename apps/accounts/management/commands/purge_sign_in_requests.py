"""Delete sign-in requests past their expiry.

Read-only by default: it reports how many rows it would delete, and
deletes them only with ``--commit``. Scheduled daily with the idempotency
purge (render.yaml).
"""

from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from apps.accounts.models import SignInRequest


class Command(BaseCommand):
    """Purge expired SignInRequest rows."""

    help = "Delete expired sign-in requests (dry run unless --commit)."

    def add_arguments(self, parser: CommandParser) -> None:
        """Add the --commit flag."""
        parser.add_argument("--commit", action="store_true", help="Actually delete the rows.")

    def handle(self, *args: Any, **options: Any) -> None:
        """Count, and with --commit delete, the expired rows."""
        expired = SignInRequest.objects.expired()
        if not options["commit"]:
            self.stdout.write(
                f"{expired.count()} expired sign-in requests (dry run; pass --commit)."
            )
            return
        deleted, _ = expired.delete()
        if options["verbosity"] > 0:
            self.stdout.write(f"Deleted {deleted} expired sign-in requests.")
