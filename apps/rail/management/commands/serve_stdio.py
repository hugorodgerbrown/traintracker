"""Run the train tools as an MCP server on stdio, for a Claude app on this machine.

What the Claude desktop app and the MCP Inspector start: the same tools as
/mcp, answering only whoever runs it, so there is no sign-in and no rate
limit. stdout carries the protocol, so the logs go to stderr (LOGGING).
"""

from typing import Any

from django.core.management.base import BaseCommand

from apps.rail import server


class Command(BaseCommand):
    """Serve the train tools on stdin and stdout."""

    help = "Run the train tools as an MCP server on stdio (the Claude desktop app)."

    def handle(self, *args: Any, **options: Any) -> None:
        """Serve until the client closes stdin."""
        server.mcp.run("stdio")
