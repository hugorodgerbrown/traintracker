"""MCP tool calls counted by fivebar, as server events (https://fiveb.ar/docs/server-events).

An MCP client calls from its own servers, so there is no page for
fivebar's tag. Instead, each ``tools/call`` sends fivebar one event, once
the tool has answered, counted as a pageview of ``/mcp/<tool name>``, so MCP calls sort
apart from the site's pages.

Off unless both ``FIVEBAR_DOMAIN`` and ``FIVEBAR_TOKEN`` (a token made in
fivebar to send events and nothing else) are set.

The event goes from a small pool of background threads with a short
timeout, so a call never waits on fivebar, and a failure is logged and
dropped. It carries the tool's name, whether it answered or failed, the
connected app's name, and a keyed hash of the user's id that tells
accounts apart without naming one. Never the arguments, the result, an
address or anything the person wrote.
See docs/decisions/visitors-are-counted-by-fivebar.md.
"""

import json
import logging
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from django.conf import settings
from django.utils.crypto import salted_hmac

from apps.core.analytics import ORIGIN

logger = logging.getLogger(__name__)

ENDPOINT = f"{ORIGIN}/api/events"
TIMEOUT = 2  # seconds; fivebar answers well inside it, and nothing waits on it
OK = "ok"
ERROR = "error"
# Every tool's event is named under this, e.g. /mcp/add_note.
PREFIX = "/mcp/"

# Two threads per process are plenty: each send is one short POST.
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="fivebar")
# At most this many events wait or are in flight per process. While fivebar
# is slow or down, any more are dropped, so an outage can't grow the queue.
MAX_PENDING = 200
_pending = threading.BoundedSemaphore(MAX_PENDING)


def caller_id(user: Any) -> str:
    """A keyed hash of the user's id: one account, one caller, and no id leaves the app."""
    digest = salted_hmac("apps.mcp.events.caller_id", str(user.pk), algorithm="sha256")
    return digest.hexdigest()[:32]


def client_name(token: Any) -> str:
    """The connected app's name, lower-cased as fivebar reads a source ("claude"), or ""."""
    application = getattr(token, "application", None)
    return (getattr(application, "name", "") or "").strip().lower()[:100]


def tool_called(user: Any, tool: str, outcome: str, client: str = "") -> None:
    """Count one tool call in fivebar, off the request path; nothing while it is off."""
    domain, token = settings.FIVEBAR_DOMAIN, settings.FIVEBAR_TOKEN
    if not (domain and token):
        return
    event: dict[str, Any] = {
        "site": domain,
        "name": f"{PREFIX}{tool}",
        "id": caller_id(user),
        "props": {"outcome": outcome},
    }
    if client:
        event["client"] = client
    if not _pending.acquire(blocking=False):
        logger.warning("fivebar.event dropped=backlog name=%s", event["name"])
        return
    _pool.submit(_send, event, token)


def _send(event: dict[str, Any], token: str) -> None:
    """POST one event to fivebar; log and drop any failure, and free its place."""
    try:
        _post(event, token)
    finally:
        _pending.release()


def _post(event: dict[str, Any], token: str) -> None:
    """The POST itself; any failure is logged, never raised."""
    # ENDPOINT is a fixed https URL, never one a caller chooses (ruff S310, semgrep).
    request = urllib.request.Request(  # noqa: S310  # nosemgrep
        ENDPOINT,
        data=json.dumps(event).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        opened = urllib.request.urlopen(request, timeout=TIMEOUT)  # noqa: S310  # nosemgrep
        with opened as response:
            status = response.status
    except urllib.error.HTTPError as exc:
        logger.warning("fivebar.event status=%s name=%s", exc.code, event["name"])
    except (OSError, ValueError) as exc:
        logger.warning("fivebar.event failed=%s name=%s", type(exc).__name__, event["name"])
    else:
        logger.debug("fivebar.event status=%s name=%s", status, event["name"])
