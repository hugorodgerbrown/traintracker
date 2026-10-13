"""The MCP tools. Each is a function of (user, arguments) returning JSON-able data.

Add a tool by writing a function and listing it in TOOLS with its JSON
Schema. Raise ToolError for a mistake the model should see and correct.

Traintrackr's train tools are defined on ``apps.rail.server``'s MCP server:
their arguments, schemas, titles and hints come from there. Each one is
listed here as a Tool whose function runs it on the train tools' event loop
(apps/rail/runtime.py), once the account's bucket allows it
(apps/rail/ratelimit.py).
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cache
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError as SdkToolError
from mcp.server.mcpserver.exceptions import UnexpectedToolError
from mcp.server.mcpserver.tools import Tool as SdkTool

from apps.rail import runtime, server
from apps.rail.ratelimit import COSTS, RateLimiter, charged, limited
from apps.rail.settings import rail_settings

logger = logging.getLogger(__name__)


class ToolError(Exception):
    """A problem with the call that the model can fix and retry."""


@dataclass(frozen=True)
class Tool:
    """One MCP tool: its name, description, input schema and implementation."""

    name: str
    description: str
    input_schema: dict[str, Any]
    func: Callable[[Any, dict[str, Any]], dict[str, Any]]
    read_only: bool = True
    # The result's "markdown" is the text content, so the caller gets the
    # document itself rather than JSON around it.
    markdown: bool = False
    # A ui:// resource in apps/mcp/resources.py that the client renders with
    # the result (an MCP App). The text content still goes to the model.
    ui: str | None = None
    # Shown to people in place of the name. Claude's directory review reads
    # it from annotations.title, so it goes there too.
    title: str | None = None
    # The JSON Schema the result (structuredContent) follows.
    output_schema: dict[str, Any] | None = None
    # Hints beyond readOnlyHint (destructiveHint, idempotentHint, openWorldHint).
    hints: dict[str, bool] = field(default_factory=dict)

    def describe(self) -> dict[str, Any]:
        """Return the tool as tools/list lists it."""
        annotations: dict[str, Any] = {"readOnlyHint": self.read_only, **self.hints}
        described: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
            "annotations": annotations,
        }
        if self.title:
            described["title"] = self.title
            annotations["title"] = self.title
        if self.output_schema:
            described["outputSchema"] = self.output_schema
        if self.ui:
            # "ui/resourceUri" is the spec's older flat key, kept until every
            # client reads the nested one.
            described["_meta"] = {"ui": {"resourceUri": self.ui}, "ui/resourceUri": self.ui}
        return described


@cache
def limiter() -> RateLimiter | None:
    """The process's buckets, one per account; None with RATE_LIMIT_PER_MINUTE=0."""
    settings = rail_settings()
    if settings.rate_limit_per_minute <= 0:
        return None
    return RateLimiter(settings.rate_limit_per_minute, settings.rate_limit_burst)


def _run_train_tool(user: Any, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Run the train tool ``name`` for ``user`` and return its structured result.

    Each account has a bucket of calls (apps/rail/ratelimit.py); over it, the
    model is told how long to wait.

    The server checks the arguments against the tool's schema and turns
    Traintrackr's own errors into messages for the model; either arrives
    here as the SDK's ToolError, whose message is written for the model. Its
    message can repeat what the caller asked, which the privacy policy keeps
    out of the logs. Anything else the SDK calls unexpected, and its message
    could carry internals, so the model gets a plain one.
    """
    buckets = limiter()
    key = str(user.pk)
    if buckets and (wait := buckets.take(key, COSTS.get(name, 1))):
        logger.info("mcp.tool name=%s limited", name)
        raise ToolError(limited(wait))
    try:
        if buckets:
            result = runtime.run(charged, buckets, key, server.mcp.call_tool, name, args)
        else:
            result = runtime.run(server.mcp.call_tool, name, args)
    except UnexpectedToolError as exc:
        logger.error("mcp.tool name=%s raised %s", name, type(exc.__cause__).__name__)
        raise ToolError("Something went wrong on the server. Try again.") from None
    except SdkToolError as exc:
        logger.info("mcp.tool name=%s failed", name)
        raise ToolError(str(exc)) from None
    except TimeoutError:
        logger.warning("mcp.tool name=%s timed out", name)
        raise ToolError("The server is busy. Try again in a few seconds.") from None
    structured = getattr(result, "structured_content", None)
    if not isinstance(structured, dict):
        raise ToolError("Something went wrong on the server. Try again.")
    return structured


def _train_tool(info: SdkTool) -> Tool:
    """List one of ``apps.rail.server``'s tools as a Tool."""
    hints = info.annotations
    ui = (info.meta or {}).get("ui", {}).get("resourceUri")
    return Tool(
        name=info.name,
        title=info.title,
        description=info.description or "",
        input_schema=info.parameters,
        output_schema=info.output_schema,
        func=lambda user, args: _run_train_tool(user, info.name, args),
        read_only=bool(hints and hints.read_only_hint),
        hints={
            "destructiveHint": bool(hints and hints.destructive_hint),
            "idempotentHint": bool(hints and hints.idempotent_hint),
            "openWorldHint": bool(hints and hints.open_world_hint),
        },
        ui=ui,
    )


NO_ARGUMENTS: dict[str, Any] = {"type": "object", "properties": {}}

# The SDK keeps its registry on the tool manager; list_tools() is the same
# list, but a coroutine, and this runs at import.
TOOLS: dict[str, Tool] = {
    t.name: t for t in [_train_tool(info) for info in server.mcp._tool_manager.list_tools()]
}
