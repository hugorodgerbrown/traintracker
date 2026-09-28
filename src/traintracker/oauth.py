"""OAuth for the hosted server, so it can be added as a claude.ai connector.

The server is its own authorization server (the MCP SDK provides the metadata,
/register, /authorize, /token and /revoke endpoints). This module supplies the
provider behind them and the one step the SDK leaves to the server: a sign-in
page. Sign-in is a single passphrase (MCP_OAUTH_PASSPHRASE): there is one user.

Clients, pending sign-ins, codes and tokens live in their own Postgres schema
(MCP_AUTH_SCHEMA, default `mcp_auth`; logged tables, so tokens survive a restart).
Codes and tokens are stored as SHA-256 hashes, never in the clear. The static
MCP_AUTH_TOKEN keeps working as a bearer token alongside OAuth.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import html
import json
import logging
import secrets
import time
from typing import Any

import psycopg
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from starlette.routing import Route

from traintracker.config import SCHEMA_NAME

log = logging.getLogger(__name__)

SIGN_IN_PATH = "/sign-in"
SIGN_IN_TTL = 10 * 60  # seconds to complete the sign-in page
CODE_TTL = 5 * 60
ACCESS_TTL = 60 * 60
REFRESH_TTL = 90 * 24 * 60 * 60
MAX_ATTEMPTS = 5  # wrong passphrases before a pending sign-in is discarded

DDL = """
CREATE SCHEMA IF NOT EXISTS {schema};
CREATE TABLE IF NOT EXISTS {schema}.clients (
    client_id TEXT PRIMARY KEY,
    info JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS {schema}.sign_ins (
    id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    params JSONB NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    expires_at DOUBLE PRECISION NOT NULL
);
CREATE TABLE IF NOT EXISTS {schema}.codes (
    code_hash TEXT PRIMARY KEY,
    data JSONB NOT NULL,
    expires_at DOUBLE PRECISION NOT NULL
);
CREATE TABLE IF NOT EXISTS {schema}.tokens (
    token_hash TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    family TEXT NOT NULL,
    data JSONB NOT NULL,
    expires_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS tokens_family ON {schema}.tokens (family);
"""


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class TraintrackerOAuthProvider(
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]
):
    """OAuth provider backed by Postgres, with passphrase sign-in.

    Subclassing the protocol inherits its default for the enterprise
    identity-assertion grant, which rejects it; this server doesn't offer it."""

    def __init__(
        self,
        dsn: str,
        public_url: str,
        passphrase: str | None,
        static_token: str | None,
        schema: str = "mcp_auth",
    ) -> None:
        if not SCHEMA_NAME.fullmatch(schema):
            raise ValueError(f"Invalid auth schema name {schema!r}.")
        self.schema = schema
        self.dsn = dsn
        self.public_url = public_url.rstrip("/")
        self.resource = f"{self.public_url}/mcp"
        self.passphrase = passphrase
        self.static_token = static_token

    # -- storage -------------------------------------------------------------

    def _run(self, *statements: tuple[str, Any], fetch: bool = False) -> list[tuple[Any, ...]]:
        """Run statements in one transaction; return the last one's rows if `fetch`."""
        rows: list[tuple[Any, ...]] = []
        with psycopg.connect(self.dsn, connect_timeout=10) as con, con.transaction():
            for query, params in statements:
                cur = con.execute(query, params)
                rows = cur.fetchall() if fetch and cur.description else []
        return rows

    async def _db(self, *statements: tuple[str, Any], fetch: bool = False) -> list[tuple[Any, ...]]:
        return await asyncio.to_thread(self._run, *statements, fetch=fetch)

    def create_tables(self) -> None:
        with psycopg.connect(self.dsn, autocommit=True, connect_timeout=10) as con:
            con.execute(DDL.format(schema=self.schema))

    # -- clients -------------------------------------------------------------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        rows = await self._db(
            (f"SELECT info FROM {self.schema}.clients WHERE client_id = %s", (client_id,)),
            fetch=True,
        )
        return OAuthClientInformationFull.model_validate(rows[0][0]) if rows else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        await self._db(
            (
                f"INSERT INTO {self.schema}.clients (client_id, info) VALUES (%s, %s)",
                (client_info.client_id, json.dumps(client_info.model_dump(mode="json"))),
            )
        )

    # -- authorization -------------------------------------------------------

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        if params.resource and params.resource.rstrip("/") != self.resource:
            raise AuthorizeError(
                "invalid_target", f"This server only issues tokens for {self.resource}."
            )
        sign_in_id = secrets.token_urlsafe(32)
        now = time.time()
        await self._db(
            (f"DELETE FROM {self.schema}.sign_ins WHERE expires_at < %s", (now,)),
            (
                f"INSERT INTO {self.schema}.sign_ins (id, client_id, params, expires_at) "
                "VALUES (%s, %s, %s, %s)",
                (sign_in_id, client.client_id, params.model_dump_json(), now + SIGN_IN_TTL),
            ),
        )
        return f"{self.public_url}{SIGN_IN_PATH}?request={sign_in_id}"

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        rows = await self._db(
            (
                f"SELECT data FROM {self.schema}.codes WHERE code_hash = %s",
                (_hash(authorization_code),),
            ),
            fetch=True,
        )
        if not rows:
            return None
        code = AuthorizationCode.model_validate(rows[0][0])
        return code if code.client_id == client.client_id else None

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        # Codes are single use: the delete must remove a row, or it was already spent.
        rows = await self._db(
            (
                f"DELETE FROM {self.schema}.codes WHERE code_hash = %s RETURNING code_hash",
                (_hash(authorization_code.code),),
            ),
            fetch=True,
        )
        if not rows:
            raise TokenError("invalid_grant", "Authorization code already used.")
        return await self._issue(client.client_id, authorization_code.scopes, secrets.token_hex(16))

    # -- tokens --------------------------------------------------------------

    async def _issue(self, client_id: str, scopes: list[str], family: str) -> OAuthToken:
        now = time.time()
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        access_data = AccessToken(
            token="",  # the stored copy never holds the token itself
            client_id=client_id,
            scopes=scopes,
            expires_at=int(now + ACCESS_TTL),
            resource=self.resource,
        )
        refresh_data = RefreshToken(
            token="", client_id=client_id, scopes=scopes, expires_at=int(now + REFRESH_TTL)
        )
        await self._db(
            (f"DELETE FROM {self.schema}.tokens WHERE expires_at < %s", (now,)),
            (
                f"INSERT INTO {self.schema}.tokens (token_hash, kind, family, data, expires_at) "
                "VALUES "
                "(%s, 'access', %s, %s, %s), (%s, 'refresh', %s, %s, %s)",
                (
                    _hash(access),
                    family,
                    access_data.model_dump_json(),
                    now + ACCESS_TTL,
                    _hash(refresh),
                    family,
                    refresh_data.model_dump_json(),
                    now + REFRESH_TTL,
                ),
            ),
        )
        return OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=ACCESS_TTL,
            refresh_token=refresh,
            scope=" ".join(scopes) or None,
        )

    async def _load(self, token: str, kind: str) -> tuple[dict[str, Any], str] | None:
        rows = await self._db(
            (
                f"SELECT data, family FROM {self.schema}.tokens "
                "WHERE token_hash = %s AND kind = %s AND expires_at > %s",
                (_hash(token), kind, time.time()),
            ),
            fetch=True,
        )
        return (rows[0][0], rows[0][1]) if rows else None

    async def load_access_token(self, token: str) -> AccessToken | None:
        if self.static_token and hmac.compare_digest(token.encode(), self.static_token.encode()):
            return AccessToken(token=token, client_id="static", scopes=[], resource=self.resource)
        found = await self._load(token, "access")
        if not found:
            return None
        return AccessToken.model_validate({**found[0], "token": token})

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        found = await self._load(refresh_token, "refresh")
        if not found or found[0]["client_id"] != client.client_id:
            return None
        return RefreshToken.model_validate({**found[0], "token": refresh_token})

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        # Rotate: the old refresh token and its access token stop working.
        rows = await self._db(
            (
                f"DELETE FROM {self.schema}.tokens WHERE family = ("
                f"SELECT family FROM {self.schema}.tokens "
                "WHERE token_hash = %s AND kind = 'refresh'"
                ") RETURNING family",
                (_hash(refresh_token.token),),
            ),
            fetch=True,
        )
        if not rows:
            raise TokenError("invalid_grant", "Refresh token already used.")
        return await self._issue(client.client_id, scopes or refresh_token.scopes, rows[0][0])

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        await self._db(
            (
                f"DELETE FROM {self.schema}.tokens WHERE family IN ("
                f"SELECT family FROM {self.schema}.tokens WHERE token_hash = %s)",
                (_hash(token.token),),
            )
        )

    # -- sign-in page --------------------------------------------------------

    async def _pending(self, sign_in_id: str) -> tuple[str, AuthorizationParams, int] | None:
        rows = await self._db(
            (
                f"SELECT client_id, params, attempts FROM {self.schema}.sign_ins "
                "WHERE id = %s AND expires_at > %s",
                (sign_in_id, time.time()),
            ),
            fetch=True,
        )
        if not rows:
            return None
        client_id, params, attempts = rows[0]
        return client_id, AuthorizationParams.model_validate(params), attempts

    async def sign_in_page(self, request: Request) -> Response:
        sign_in_id = request.query_params.get("request", "")
        pending = await self._pending(sign_in_id)
        if not pending:
            return _page("This sign-in link has expired. Start again from Claude.", status=400)
        client = await self.get_client(pending[0])
        name = (client.client_name if client else None) or "An MCP client"
        return _form(sign_in_id, name)

    async def sign_in_submit(self, request: Request) -> Response:
        form = await request.form()
        sign_in_id = str(form.get("request", ""))
        given = str(form.get("passphrase", ""))
        pending = await self._pending(sign_in_id)
        if not pending:
            return _page("This sign-in link has expired. Start again from Claude.", status=400)
        client_id, params, attempts = pending
        if not self.passphrase:
            return _page("Sign-in is turned off on this server (MCP_OAUTH_PASSPHRASE is not set).")
        if not hmac.compare_digest(given.encode(), self.passphrase.encode()):
            await asyncio.sleep(1)  # slow down guessing
            if attempts + 1 >= MAX_ATTEMPTS:
                await self._db((f"DELETE FROM {self.schema}.sign_ins WHERE id = %s", (sign_in_id,)))
                log.warning(
                    "Sign-in %s discarded after %d wrong passphrases", sign_in_id[:6], MAX_ATTEMPTS
                )
                return _page("Too many wrong passphrases. Start again from Claude.", status=403)
            await self._db(
                (
                    f"UPDATE {self.schema}.sign_ins SET attempts = attempts + 1 WHERE id = %s",
                    (sign_in_id,),
                )
            )
            client = await self.get_client(client_id)
            name = (client.client_name if client else None) or "An MCP client"
            return _form(sign_in_id, name, error="Wrong passphrase.", status=401)

        code = secrets.token_urlsafe(32)
        auth_code = AuthorizationCode(
            code=code,
            scopes=params.scopes or [],
            expires_at=time.time() + CODE_TTL,
            client_id=client_id,
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=self.resource,
        )
        await self._db(
            (f"DELETE FROM {self.schema}.sign_ins WHERE id = %s", (sign_in_id,)),
            (
                f"INSERT INTO {self.schema}.codes (code_hash, data, expires_at) "
                "VALUES (%s, %s, %s)",
                (_hash(code), auth_code.model_dump_json(), auth_code.expires_at),
            ),
        )
        target = construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state)
        return RedirectResponse(target, status_code=302)

    def routes(self) -> list[Route]:
        return [
            Route(SIGN_IN_PATH, self.sign_in_page, methods=["GET"]),
            Route(SIGN_IN_PATH, self.sign_in_submit, methods=["POST"]),
        ]


# -- HTML -----------------------------------------------------------------------

# The sign-in page is only reached from an OAuth flow and is never shared, so it
# opts out of link unfurling (noindex, no Open Graph tags) and of framing.
_HEAD = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Sign in · traintracker</title>
<style>
  body { font: 16px/1.5 system-ui, sans-serif; max-width: 26rem;
         margin: 4rem auto; padding: 0 1rem; }
  input, button { font: inherit; padding: .5rem; width: 100%; box-sizing: border-box; }
  button { margin-top: .75rem; cursor: pointer; }
  .error { color: #b00020; }
</style></head><body>
"""

_HEADERS = {
    "Cache-Control": "no-store",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'",
    "Referrer-Policy": "same-origin",
}


def _page(message: str, status: int = 200) -> HTMLResponse:
    body = f"{_HEAD}<h1>traintracker</h1><p>{html.escape(message)}</p></body></html>"
    return HTMLResponse(body, status_code=status, headers=_HEADERS)


def _form(sign_in_id: str, client_name: str, error: str = "", status: int = 200) -> HTMLResponse:
    body = (
        f"{_HEAD}<h1>traintracker</h1>"
        f"<p><strong>{html.escape(client_name)}</strong> is asking to use traintracker.</p>"
        + (f'<p class="error" role="alert">{html.escape(error)}</p>' if error else "")
        + f'<form method="post" action="{SIGN_IN_PATH}">'
        f'<input type="hidden" name="request" value="{html.escape(sign_in_id)}">'
        '<label for="passphrase">Passphrase</label>'
        '<input id="passphrase" name="passphrase" type="password" autocomplete="current-password" '
        "required autofocus>"
        "<button type=submit>Allow</button></form></body></html>"
    )
    return HTMLResponse(body, status_code=status, headers=_HEADERS)


async def health(_request: Request) -> Response:
    return PlainTextResponse("ok")
