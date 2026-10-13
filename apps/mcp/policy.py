"""Who may connect an MCP client to this project (MCP_AUTH["CAN_CONNECT"]).

Staff only to start with: an MCP client acts with the user's full access,
so widen this deliberately, as a product decision. It runs on the consent
page and on every call, so revoking staff status cuts a user's clients off.
"""

from typing import Any


def staff_only(user: Any) -> bool:
    """An active staff member may connect."""
    return bool(user.is_active and user.is_staff)
