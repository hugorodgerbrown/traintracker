"""Say which rail data sources are set up, how much of Darwin is used, and the timetable.

It only reads.
"""

from typing import Any

from django.core.management.base import BaseCommand

from apps.rail.server import status_text
from apps.rail.settings import rail_settings


class Command(BaseCommand):
    """Print the rail data status."""

    help = "Show the rail data sources, Darwin usage and the timetable's build."

    def handle(self, *args: Any, **options: Any) -> None:
        """Print the status."""
        self.stdout.write(status_text(rail_settings()))
