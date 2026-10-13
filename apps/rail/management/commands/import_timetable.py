"""Build the timetable from a SCHEDULE file already on disk.

For a feed downloaded by hand, or a test feed. It replaces the timetable schema, so it only does
so with ``--commit``.
"""

from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.rail.errors import TrainTrackerError
from apps.rail.settings import rail_settings
from apps.rail.timetable import build_from_file


class Command(BaseCommand):
    """Rebuild the timetable from a SCHEDULE JSON file, gzipped or not."""

    help = "Build the timetable from a SCHEDULE file (dry run unless --commit)."

    def add_arguments(self, parser: CommandParser) -> None:
        """Add the file and the --commit flag."""
        parser.add_argument("file", type=Path, help="CIF_ALL_FULL_DAILY JSON, gzipped or not.")
        parser.add_argument("--commit", action="store_true", help="Build the timetable.")

    def handle(self, *args: Any, **options: Any) -> None:
        """Build the timetable schema from the file, or check the file without --commit."""
        path: Path = options["file"]
        if not path.is_file():
            raise CommandError(f"No such file: {path}")
        settings = rail_settings()
        schema = settings.timetable_db.schema
        if not options["commit"]:
            self.stdout.write(
                f"Would rebuild timetable schema {schema} from {path} (pass --commit)."
            )
            return
        try:
            meta = build_from_file(path, settings.timetable_db)
        except TrainTrackerError as exc:
            raise CommandError(str(exc)) from exc
        if options["verbosity"] > 0:
            self.stdout.write(f"Timetable built in schema {schema}: {meta}")
