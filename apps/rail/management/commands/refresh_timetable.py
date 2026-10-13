"""Download Network Rail's daily SCHEDULE extract and rebuild the timetable.

``traintracker refresh`` as a management command, for the Render cron job
after the cut-over. It replaces the timetable schema, so it only does so
with ``--commit``; without it, it says what it would do. In demo mode it
rebuilds the generated timetable instead.
"""

import asyncio
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.rail.settings import rail_settings
from traintracker import demo
from traintracker.errors import TrainTrackerError
from traintracker.timetable import download_and_build


class Command(BaseCommand):
    """Rebuild the timetable from Network Rail (or the demo feed)."""

    help = "Download the SCHEDULE extract and rebuild the timetable (dry run unless --commit)."

    def add_arguments(self, parser: CommandParser) -> None:
        """Add the --commit flag."""
        parser.add_argument("--commit", action="store_true", help="Download and rebuild.")

    def handle(self, *args: Any, **options: Any) -> None:
        """Rebuild the timetable schema, or describe the rebuild without --commit."""
        settings = rail_settings()
        source = "the generated demo feed" if settings.demo else "Network Rail's daily extract"
        schema = settings.timetable_db.schema
        if not options["commit"]:
            self.stdout.write(
                f"Would rebuild timetable schema {schema} from {source} (pass --commit)."
            )
            return
        try:
            meta = (
                demo.build_timetable(settings)
                if settings.demo
                else asyncio.run(download_and_build(settings))
            )
        except TrainTrackerError as exc:
            raise CommandError(str(exc)) from exc
        if options["verbosity"] > 0:
            self.stdout.write(f"Timetable built in schema {schema}: {meta}")
