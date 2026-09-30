"""The MCP App: a live board drawn as a split-flap departure board.

A client that supports MCP Apps (SEP-1865) shows board.html in a sandboxed
frame next to the result of a tool that names it, and hands the page that
result. A client that doesn't ignores it and the tool answers as it always
has, so the board is an extra, never the only form of the answer.

board.html is one file with its style and script inline and no build step. It
loads nothing from anywhere, which is what a client allows a ui:// resource by
default.
"""

from __future__ import annotations

from importlib import resources

from mcp.server.apps import Apps

BOARD_URI = "ui://traintracker/board.html"


def board_html() -> str:
    return resources.files(__name__).joinpath("board.html").read_text("utf-8")


def apps() -> Apps:
    """The extension that serves the board and tells clients this server has apps."""
    extension = Apps()
    extension.add_html_resource(
        BOARD_URI,
        board_html(),
        name="departure_board",
        title="Departure board",
        description="A station's live departures or arrivals as a split-flap board.",
        # The page draws its own panel.
        prefers_border=False,
    )
    return extension
