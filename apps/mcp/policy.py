"""Who may connect an MCP client to this project (MCP_AUTH["CAN_CONNECT"]).

Titan apps let any active account connect (``mcp_auth.policy.active_user``,
the copier default). This is the narrower choice, for an app that keeps
MCP to staff: it runs on the consent page and on every call, so revoking
staff status cuts a user's clients off.
"""

from typing import Any


def staff_only(user: Any) -> bool:
    """An active staff member may connect."""
    return bool(user.is_active and user.is_staff)
