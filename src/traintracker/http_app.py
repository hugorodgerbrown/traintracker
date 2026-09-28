"""Streamable-HTTP entry point for hosting the server (e.g. on Render).

Every request except the health check must carry `Authorization: Bearer <token>`
matching MCP_AUTH_TOKEN, so the Darwin and Network Rail credentials behind the
server can't be used by anyone who finds the URL.
"""

from __future__ import annotations

import hmac
from typing import Any

HEALTH_PATH = "/healthz"

Scope = dict[str, Any]
Receive = Any
Send = Any
ASGIApp = Any


class BearerAuth:
    """ASGI middleware: reject HTTP requests without the expected bearer token."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        if not token:
            raise ValueError("An empty bearer token would accept every request.")
        self.app = app
        self.expected = f"Bearer {token}".encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            if scope["path"] == HEALTH_PATH:
                await _respond(send, 200, b"ok")
                return
            given = dict(scope["headers"]).get(b"authorization", b"")
            if not hmac.compare_digest(given, self.expected):
                await _respond(send, 401, b"Missing or wrong bearer token.")
                return
        await self.app(scope, receive, send)


async def _respond(send: Send, status: int, body: bytes) -> None:
    headers = [(b"content-type", b"text/plain; charset=utf-8")]
    if status == 401:
        headers.append((b"www-authenticate", b"Bearer"))
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


def serve(app: ASGIApp, token: str, host: str, port: int) -> None:
    import uvicorn

    uvicorn.run(BearerAuth(app, token), host=host, port=port, proxy_headers=True)
