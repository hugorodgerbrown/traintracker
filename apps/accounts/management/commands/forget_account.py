"""Delete an account and everything kept about it, when its owner asks.

``forget_account EMAIL`` says what it would delete; ``--commit`` deletes
the account, which takes its passkeys and its connected apps' tokens with
it, and any sign-in request for the address. Both are found by the
address's key, which is all that is stored. Traintrackr's privacy policy
promises this on request.
"""

from typing import Any

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.accounts.models import SignInRequest, email_key


class Command(BaseCommand):
    """Forget one email address."""

    help = "Delete the account for EMAIL and what is kept about it (dry run unless --commit)."

    def add_arguments(self, parser: CommandParser) -> None:
        """Add the address and the --commit flag."""
        parser.add_argument("email", help="The account's email address.")
        parser.add_argument("--commit", action="store_true", help="Actually delete it.")

    def handle(self, *args: Any, **options: Any) -> None:
        """Find, and with --commit delete, the account and its sign-in requests."""
        users = get_user_model().objects.filter(username=email_key(options["email"]))
        requests = SignInRequest.objects.for_email(options["email"])
        if not users.exists() and not requests.exists():
            raise CommandError("No account or sign-in request has that address.")
        if users.filter(is_staff=True).exists():
            raise CommandError("That is a staff account: remove it in the admin.")
        if not options["commit"]:
            self.stdout.write(
                f"Would delete {users.count()} account(s) and {requests.count()} sign-in "
                "request(s) (dry run; pass --commit)."
            )
            return
        requests.delete()
        users.delete()
        if options["verbosity"] > 0:
            self.stdout.write("Deleted the account and everything kept about it.")
