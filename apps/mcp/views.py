"""The MCP endpoint: JSON-RPC over the Streamable HTTP transport.

Stateless and synchronous: every request is a POST answered with one JSON
body, so it runs under gunicorn's sync workers like the rest of the site.
There is no server-to-client stream; GET answers 405 as the spec allows.
Tools may name a ui:// resource (apps/mcp/resources.py) that the client
renders with their result: an MCP App.
Authentication is mcp_auth's ``@mcp_endpoint``: a bearer token bound to
this URL, or a 401 that starts OAuth discovery.
"""

import json
import logging
from typing import Any

from django.http import HttpRequest, HttpResponse, JsonResponse
from mcp_auth.resource import mcp_endpoint

from apps.mcp.resources import MIME_TYPE, RESOURCES, ResourceNotFound, read_resource
from apps.mcp.tools import TOOLS, ToolError
from apps.pwa.conf import APP_NAME
from traintracker import __version__, server

logger = logging.getLogger(__name__)

SUPPORTED_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")
SERVER_INFO = {"name": "traintracker", "title": APP_NAME, "version": __version__}
# The same instructions as production's MCP server gives.
INSTRUCTIONS = server.INSTRUCTIONS

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
RESOURCE_NOT_FOUND = -32002


class RpcError(Exception):
    """A JSON-RPC error with its code."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@mcp_endpoint
def mcp(request: HttpRequest) -> HttpResponse:
    """Answer one JSON-RPC message as the token's user."""
    version = request.headers.get("MCP-Protocol-Version")
    if version and version not in SUPPORTED_VERSIONS:
        return _error_response(None, INVALID_REQUEST, f"Unsupported protocol version {version}")

    try:
        message = json.loads(request.body)
    except ValueError:
        return _error_response(None, PARSE_ERROR, "Parse error")
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _error_response(None, INVALID_REQUEST, "Expected one JSON-RPC 2.0 message")

    # Notifications and responses from the client need no answer.
    if "method" not in message or "id" not in message:
        return HttpResponse(status=202)

    msg_id = message["id"]
    try:
        result = _dispatch(request.user, message["method"], message.get("params") or {})
    except RpcError as exc:
        return _error_response(msg_id, exc.code, exc.message)
    return JsonResponse({"jsonrpc": "2.0", "id": msg_id, "result": result})


def _error_response(msg_id: Any, code: int, message: str) -> JsonResponse:
    """Return a JSON-RPC error; HTTP 400 when there is no id to answer."""
    status = 400 if msg_id is None else 200
    return JsonResponse(
        {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}},
        status=status,
    )


def _dispatch(user: Any, method: str, params: Any) -> dict[str, Any]:
    """Route a JSON-RPC method to its handler."""
    if not isinstance(params, dict):
        raise RpcError(INVALID_PARAMS, "params must be an object")
    match method:
        case "initialize":
            requested = params.get("protocolVersion")
            return {
                "protocolVersion": requested
                if requested in SUPPORTED_VERSIONS
                else SUPPORTED_VERSIONS[0],
                "capabilities": {
                    "tools": {"listChanged": False},
                    "resources": {"listChanged": False},
                    # MCP Apps is an extension; both sides say so (SEP-1724).
                    "extensions": {"io.modelcontextprotocol/ui": {"mimeTypes": [MIME_TYPE]}},
                },
                "serverInfo": SERVER_INFO,
                "instructions": INSTRUCTIONS,
            }
        case "ping":
            return {}
        case "tools/list":
            return {"tools": [t.describe() for t in TOOLS.values()]}
        case "tools/call":
            return _call_tool(user, params)
        case "resources/list":
            return {"resources": [r.describe() for r in RESOURCES.values()]}
        case "resources/templates/list":
            return {"resourceTemplates": []}
        case "resources/read":
            return _read_resource(params)
        case _:
            raise RpcError(METHOD_NOT_FOUND, f"Method not found: {method}")


def _read_resource(params: dict[str, Any]) -> dict[str, Any]:
    """Return one ui:// resource; the same for every user, as it holds no data."""
    uri = params.get("uri")
    if not isinstance(uri, str):
        raise RpcError(INVALID_PARAMS, "uri must be a string")
    try:
        return read_resource(uri)
    except ResourceNotFound:
        raise RpcError(RESOURCE_NOT_FOUND, f"Resource not found: {uri}") from None


def _call_tool(user: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Run one tool; a ToolError becomes an isError result the model can read."""
    name = params.get("name")
    tool = TOOLS.get(name) if isinstance(name, str) else None
    if tool is None:
        raise RpcError(INVALID_PARAMS, f"Unknown tool: {name}")
    args = params.get("arguments") or {}
    if not isinstance(args, dict):
        raise RpcError(INVALID_PARAMS, "arguments must be an object")
    try:
        result = tool.func(user, args)
    except ToolError as exc:
        return {"content": [{"type": "text", "text": str(exc)}], "isError": True}
    logger.info("mcp.tool name=%s", tool.name)
    if tool.markdown:
        content = {"type": "text", "text": result["markdown"]}
    else:
        content = {"type": "text", "text": json.dumps(result, ensure_ascii=False)}
    return {"content": [content], "structuredContent": result}
