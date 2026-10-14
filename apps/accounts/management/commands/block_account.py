"""Stop an email address signing in, and its connected apps working.

``block_account EMAIL --commit`` closes the account (``is_active`` off),
making one for the address if there is none yet, so its first sign-in is
refused too. Its connected apps stop at once: mcp_auth's rule
(MCP_AUTH CAN_CONNECT) runs on every call. Reopen it in the admin.
"""

from typing import Any

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.accounts.models import email_key


class Command(BaseCommand):
    """Block one email address."""

    help = "Close the account for EMAIL, or make a closed one (dry run unless --commit)."

    def add_arguments(self, parser: CommandParser) -> None:
        """Add the address and the --commit flag."""
        parser.add_argument("email", help="The email address to block.")
        parser.add_argument("--commit", action="store_true", help="Actually block it.")

    def handle(self, *args: Any, **options: Any) -> None:
        """Close the address's account, making it if need be."""
        key = email_key(options["email"])
        user_model = get_user_model()
        user = user_model.objects.filter(username=key).first()
        if user is not None and user.is_staff:
            raise CommandError("That is a staff account: change it in the admin.")
        if not options["commit"]:
            what = "close its account" if user else "make a closed account for it"
            self.stdout.write(f"Would {what} (dry run; pass --commit).")
            return
        if user is None:
            user = user_model(username=key, email="", is_active=False)
            user.set_unusable_password()
            user.save()
        else:
            user.is_active = False
            user.save(update_fields=["is_active"])
        if options["verbosity"] > 0:
            self.stdout.write("Blocked: it can't sign in, and its connected apps have stopped.")
