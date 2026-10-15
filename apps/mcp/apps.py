"""App config for apps.mcp."""

from django.apps import AppConfig


class McpConfig(AppConfig):
    """The MCP server. Authentication is mcp_auth's; the tools are ours."""

    name = "apps.mcp"
    label = "mcp"
