"""App config for apps.mcp."""

from django.apps import AppConfig


class McpConfig(AppConfig):
    """The MCP server. Authentication is mcp_auth's; the tools are ours."""

    name = "apps.mcp"
    label = "mcp"

    def ready(self) -> None:
        """Let ChatGPT connect although its client metadata prefers private_key_jwt."""
        from .cimd import accept_public_fallback

        accept_public_fallback()
