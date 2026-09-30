"""OAuth for the hosted server, so it can be added as a claude.ai connector.

The server is its own authorization server (the MCP SDK provides the metadata,
/register, /authorize, /token and /revoke endpoints). This module supplies the
provider behind them and the one step the SDK leaves to the server: a sign-in
page. There are two ways in:

- a six-digit code sent to the person's email address, for the public. The
  address goes to the mail provider and is not stored: the account is a keyed
  hash of it (MCP_ACCOUNT_SECRET), which is all the rate limiter needs.
- a single passphrase (MCP_OAUTH_PASSPHRASE), for the owner and for directory
  reviewers, who need credentials that work without a mailbox.

Clients, pending sign-ins, codes, tokens and accounts live in their own Postgres
schema (MCP_AUTH_SCHEMA, default `mcp_auth`; logged tables, so tokens survive a
restart). Codes and tokens are stored as SHA-256 hashes, never in the clear. The
static MCP_AUTH_TOKEN keeps working as a bearer token alongside OAuth.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import hmac
import html
import json
import logging
import re
import secrets
import time
from typing import Any
from urllib.parse import urlparse

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

from traintracker import site
from traintracker.config import SCHEMA_NAME
from traintracker.mail import CODE_MINUTES, Mailer, MailError

log = logging.getLogger(__name__)

SIGN_IN_PATH = "/sign-in"
EMAIL_PATH = "/sign-in/email"
LOGIN_CODE_PATH = "/sign-in/code"
SIGN_IN_TTL = 10 * 60  # seconds to complete the sign-in page
CODE_TTL = 5 * 60
ACCESS_TTL = 60 * 60
REFRESH_TTL = 90 * 24 * 60 * 60
MAX_ATTEMPTS = 5  # wrong passphrases (or wrong codes) before a sign-in is discarded
LOGIN_CODE_TTL = CODE_MINUTES * 60
MAX_SENDS = 3  # codes one sign-in may ask for
MAX_CODES_PER_ADDRESS = 5  # codes one address may be sent in an hour
MAIL_LOG_TTL = 24 * 60 * 60
ACCOUNT_IDLE_DAYS = 180  # an account not used for this long is deleted
OFFLINE_ACCESS = "offline_access"  # the scope that asks for a refresh token
PASSPHRASE_SUBJECT = "passphrase"  # the account everyone using the passphrase shares
BLOCKED = "This address can't sign in to this server."
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")

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
ALTER TABLE {schema}.sign_ins ADD COLUMN IF NOT EXISTS account TEXT;
ALTER TABLE {schema}.sign_ins ADD COLUMN IF NOT EXISTS login_code_hash TEXT;
ALTER TABLE {schema}.sign_ins ADD COLUMN IF NOT EXISTS login_code_expires_at DOUBLE PRECISION;
ALTER TABLE {schema}.sign_ins ADD COLUMN IF NOT EXISTS login_code_attempts INTEGER
    NOT NULL DEFAULT 0;
ALTER TABLE {schema}.sign_ins ADD COLUMN IF NOT EXISTS sends INTEGER NOT NULL DEFAULT 0;
CREATE TABLE IF NOT EXISTS {schema}.accounts (
    id TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    blocked BOOLEAN NOT NULL DEFAULT false
);
CREATE TABLE IF NOT EXISTS {schema}.mail_log (
    account TEXT NOT NULL,
    sent_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS mail_log_sent_at ON {schema}.mail_log (sent_at);
"""


Statement = tuple[str, Any] | tuple[str, Any, bool]


class _Missing(Exception):
    """A required row was not there; the transaction was rolled back."""


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class TraintrackerOAuthProvider(
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]
):
    """OAuth provider backed by Postgres, with email-code and passphrase sign-in.

    Subclassing the protocol inherits its default for the enterprise
    identity-assertion grant, which rejects it; this server doesn't offer it."""

    def __init__(
        self,
        dsn: str,
        public_url: str,
        passphrase: str | None,
        static_token: str | None,
        schema: str = "mcp_auth",
        account_secret: str | None = None,
        mailer: Mailer | None = None,
        mail_max_per_hour: int = 200,
    ) -> None:
        if not SCHEMA_NAME.fullmatch(schema):
            raise ValueError(f"Invalid auth schema name {schema!r}.")
        self.schema = schema
        self.dsn = dsn
        self.public_url = public_url.rstrip("/")
        self.resource = f"{self.public_url}/mcp"
        self.site = urlparse(self.public_url).hostname or self.public_url
        self.passphrase = passphrase
        self.static_token = static_token
        self.account_secret = account_secret
        # Email sign-in needs both: somewhere to send the code, and the key
        # that turns the address into an account.
        self.mailer = mailer if account_secret else None
        self.mail_max_per_hour = mail_max_per_hour

    # -- storage -------------------------------------------------------------

    def _run(self, *statements: Statement, fetch: bool = False) -> list[tuple[Any, ...]]:
        """Run statements in one transaction; return the last one's rows if `fetch`.

        A statement marked `required` must return a row; if it doesn't, the whole
        transaction rolls back and _Missing is raised (e.g. a code already spent).
        """
        rows: list[tuple[Any, ...]] = []
        with psycopg.connect(self.dsn, connect_timeout=10) as con, con.transaction():
            for query, params, *required in statements:
                cur = con.execute(query, params)
                rows = cur.fetchall() if cur.description else []
                if required and required[0] and not rows:
                    raise _Missing
        return rows if fetch else []

    async def _db(self, *statements: Statement, fetch: bool = False) -> list[tuple[Any, ...]]:
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
        if not rows:
            return None
        client = OAuthClientInformationFull.model_validate(rows[0][0])
        # The metadata lists offline_access, so clients ask for it at /authorize.
        # The SDK only lets a client ask for scopes it registered with, and most
        # register with none (every client registered before the scope was
        # listed did), so every client is allowed it here.
        scopes = (client.scope or "").split()
        if OFFLINE_ACCESS not in scopes:
            client.scope = " ".join([*scopes, OFFLINE_ACCESS])
        return client

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
        # The stored copy has no code; put back the one the client presented.
        code = AuthorizationCode.model_validate({**rows[0][0], "code": authorization_code})
        return code if code.client_id == client.client_id else None

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        # Codes are single use: spending the code and issuing tokens happen in one
        # transaction, so a failed insert leaves the code usable.
        issue, token = self._issue(
            client.client_id, authorization_code.scopes, authorization_code.subject
        )
        spend: Statement = (
            f"DELETE FROM {self.schema}.codes WHERE code_hash = %s RETURNING code_hash",
            (_hash(authorization_code.code),),
            True,
        )
        await self._purge_expired_tokens()
        try:
            await self._db(spend, *issue)
        except _Missing:
            raise TokenError("invalid_grant", "Authorization code already used.") from None
        return token

    # -- tokens --------------------------------------------------------------

    def _issue(
        self, client_id: str, scopes: list[str], subject: str | None = None
    ) -> tuple[list[Statement], OAuthToken]:
        """Statements that store a new access/refresh pair, and the token to return.

        The caller runs them in the same transaction as whatever the pair replaces.
        `subject` is the account that signed in; it stays with the pair through
        every refresh, so requests can be counted per account.
        """
        now = time.time()
        family = secrets.token_hex(16)
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        access_data = AccessToken(
            token="",  # the stored copy never holds the token itself
            client_id=client_id,
            scopes=scopes,
            expires_at=int(now + ACCESS_TTL),
            resource=self.resource,
            subject=subject,
        )
        refresh_data = RefreshToken(
            token="",
            client_id=client_id,
            scopes=scopes,
            expires_at=int(now + REFRESH_TTL),
            subject=subject,
        )
        statements: list[Statement] = [
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
        ]
        token = OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=ACCESS_TTL,
            refresh_token=refresh,
            scope=" ".join(scopes) or None,
        )
        return statements, token

    async def _purge_expired_tokens(self) -> None:
        """Delete expired tokens in a transaction of its own.

        Kept out of the rotation transaction: run after a family's rows are
        locked, this table-wide delete could wait on another rotation's locked
        (expired) rows while holding its own, and deadlock.
        """
        await self._db((f"DELETE FROM {self.schema}.tokens WHERE expires_at < %s", (time.time(),)))

    async def _load(self, token: str, kind: str) -> tuple[dict[str, Any], str] | None:
        # A blocked account's tokens stop working at once, not when they expire.
        rows = await self._db(
            (
                f"SELECT data, family FROM {self.schema}.tokens t "
                "WHERE token_hash = %s AND kind = %s AND expires_at > %s AND NOT EXISTS ("
                f"SELECT 1 FROM {self.schema}.accounts a "
                "WHERE a.id = t.data->>'subject' AND a.blocked)",
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
        # Rotate: the old refresh token and its access token stop working. Retiring
        # the old pair and storing the new one is one transaction, so a failure
        # leaves the client's current tokens in place.
        issue, token = self._issue(
            client.client_id, scopes or refresh_token.scopes, refresh_token.subject
        )
        retire: Statement = (
            f"DELETE FROM {self.schema}.tokens WHERE family = ("
            f"SELECT family FROM {self.schema}.tokens "
            "WHERE token_hash = %s AND kind = 'refresh'"
            ") RETURNING family",
            (_hash(refresh_token.token),),
            True,
        )
        # A refresh counts as use, so an account in use is never deleted as idle.
        seen: Statement = (
            f"UPDATE {self.schema}.accounts SET last_seen_at = now() WHERE id = %s",
            (refresh_token.subject,),
        )
        await self._purge_expired_tokens()
        try:
            await self._db(retire, *issue, seen)
        except _Missing:
            raise TokenError("invalid_grant", "Refresh token already used.") from None
        return token

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        await self._db(
            (
                f"DELETE FROM {self.schema}.tokens WHERE family IN ("
                f"SELECT family FROM {self.schema}.tokens WHERE token_hash = %s)",
                (_hash(token.token),),
            )
        )

    # -- sign-in page --------------------------------------------------------

    async def _pending(self, sign_in_id: str) -> _Pending | None:
        rows = await self._db(
            (
                f"SELECT client_id, params, attempts, sends FROM {self.schema}.sign_ins "
                "WHERE id = %s AND expires_at > %s",
                (sign_in_id, time.time()),
            ),
            fetch=True,
        )
        if not rows:
            return None
        client_id, params, attempts, sends = rows[0]
        client = await self.get_client(client_id)
        return _Pending(
            sign_in_id=sign_in_id,
            client_id=client_id,
            client_name=(client.client_name if client else None) or "An MCP client",
            params=AuthorizationParams.model_validate(params),
            attempts=attempts,
            sends=sends,
        )

    def _form(self, pending: _Pending, error: str = "", status: int = 200) -> HTMLResponse:
        return _form(
            pending,
            email=self.mailer is not None,
            passphrase=self.passphrase is not None,
            error=error,
            status=status,
        )

    async def sign_in_page(self, request: Request) -> Response:
        pending = await self._pending(request.query_params.get("request", ""))
        if not pending:
            return _expired()
        return self._form(pending)

    async def sign_in_submit(self, request: Request) -> Response:
        """The passphrase route: the owner's and the reviewers' way in."""
        form = await request.form()
        sign_in_id = str(form.get("request", ""))
        given = str(form.get("passphrase", ""))
        if not self.passphrase:
            return _page("Sign-in is turned off on this server (MCP_OAUTH_PASSPHRASE is not set).")
        # Take an attempt before checking the passphrase. The conditional UPDATE
        # locks the row, so parallel submissions can't share one attempt: at most
        # MAX_ATTEMPTS passphrases are ever checked per sign-in.
        rows = await self._db(
            (
                f"UPDATE {self.schema}.sign_ins SET attempts = attempts + 1 "
                "WHERE id = %s AND expires_at > %s AND attempts < %s "
                "RETURNING attempts",
                (sign_in_id, time.time(), MAX_ATTEMPTS),
            ),
            fetch=True,
        )
        pending = await self._pending(sign_in_id) if rows else None
        if not pending:
            return _expired()
        if not hmac.compare_digest(given.encode(), self.passphrase.encode()):
            await asyncio.sleep(1)  # slow down guessing
            if rows[0][0] >= MAX_ATTEMPTS:
                return await self._discard(sign_in_id, "Too many wrong passphrases.", 403)
            return self._form(pending, error="Wrong passphrase.", status=401)
        return await self._grant(pending, PASSPHRASE_SUBJECT)

    async def send_code(self, request: Request) -> Response:
        """Email a sign-in code to the address given on the sign-in page."""
        form = await request.form()
        pending = await self._pending(str(form.get("request", "")))
        address = str(form.get("email", "")).strip()
        if not self.mailer:
            return _page("Email sign-in is turned off on this server.")
        if not pending:
            return _expired()
        if len(address) > 254 or not _EMAIL.fullmatch(address):
            return self._form(pending, error="Enter a full email address.", status=400)
        if pending.sends >= MAX_SENDS:
            return await self._discard(pending.sign_in_id, "Too many codes requested.", 429)
        account = self.account_id(address)
        if await self._blocked(account):
            return _page(BLOCKED, status=403)
        code = f"{secrets.randbelow(1_000_000):06d}"
        now = time.time()
        try:
            await self._db(
                # One permit at a time. Without the lock, sign-ins asking at the
                # same moment would each count the log before any had added to
                # it, and all be let through. It is held until the commit.
                ("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"{self.schema}.mail_log",)),
                (f"DELETE FROM {self.schema}.mail_log WHERE sent_at < %s", (now - MAIL_LOG_TTL,)),
                # The log row is the send permit: it is only written while this
                # address and the server as a whole are under their hourly caps.
                (
                    f"INSERT INTO {self.schema}.mail_log (account, sent_at) SELECT %s, %s WHERE "
                    f"(SELECT count(*) FROM {self.schema}.mail_log "
                    "WHERE account = %s AND sent_at > %s) < %s AND "
                    f"(SELECT count(*) FROM {self.schema}.mail_log WHERE sent_at > %s) < %s "
                    "RETURNING account",
                    (
                        account,
                        now,
                        account,
                        now - 3600,
                        MAX_CODES_PER_ADDRESS,
                        now - 3600,
                        self.mail_max_per_hour,
                    ),
                    True,
                ),
                # The sign-in is kept open for as long as its code lasts: a code
                # asked for late in the sign-in's own ten minutes must still work.
                (
                    f"UPDATE {self.schema}.sign_ins SET sends = sends + 1, account = %s, "
                    "login_code_hash = %s, login_code_expires_at = %s, login_code_attempts = 0, "
                    "expires_at = GREATEST(expires_at, %s) "
                    "WHERE id = %s AND sends < %s RETURNING id",
                    (
                        account,
                        _login_code_hash(pending.sign_in_id, code),
                        now + LOGIN_CODE_TTL,
                        now + LOGIN_CODE_TTL,
                        pending.sign_in_id,
                        MAX_SENDS,
                    ),
                    True,
                ),
            )
        except _Missing:
            return self._form(
                pending, error="Too many codes have been sent. Try again in an hour.", status=429
            )
        try:
            await self.mailer.send_code(address, code, self.site)
        except MailError as exc:
            log.warning("Sign-in code not sent: %s", exc)
            return self._form(
                pending, error="The code could not be sent. Try again shortly.", status=502
            )
        return _code_form(pending, address)

    async def check_code(self, request: Request) -> Response:
        """Finish an email sign-in: the code proves the address can be read."""
        form = await request.form()
        sign_in_id = str(form.get("request", ""))
        address = str(form.get("email", "")).strip()
        given = re.sub(r"\s", "", str(form.get("code", "")))
        now = time.time()
        # One attempt per submission, taken under the row lock, as for passphrases.
        rows = await self._db(
            (
                f"UPDATE {self.schema}.sign_ins SET login_code_attempts = login_code_attempts + 1 "
                "WHERE id = %s AND expires_at > %s AND login_code_hash IS NOT NULL "
                "AND login_code_attempts < %s "
                "RETURNING login_code_attempts, login_code_hash, login_code_expires_at, account",
                (sign_in_id, now, MAX_ATTEMPTS),
            ),
            fetch=True,
        )
        pending = await self._pending(sign_in_id) if rows else None
        if not pending:
            return _expired()
        attempts, code_hash, expires_at, account = rows[0]
        right = hmac.compare_digest(_login_code_hash(sign_in_id, given), code_hash)
        if not right or expires_at < now:
            await asyncio.sleep(1)  # slow down guessing
            if attempts >= MAX_ATTEMPTS:
                return await self._discard(sign_in_id, "Too many wrong codes.", 403)
            error = "Wrong code." if not right else "That code has expired. Send a new one."
            return _code_form(pending, address, error=error, status=401)
        if not await self._record_sign_in(account):
            return _page(BLOCKED, status=403)
        return await self._grant(pending, account)

    async def _record_sign_in(self, account: str) -> bool:
        """Note the sign-in and drop accounts nobody has used; False if blocked."""
        rows = await self._db(
            (
                f"DELETE FROM {self.schema}.tokens WHERE data->>'subject' IN ("
                f"SELECT id FROM {self.schema}.accounts "
                "WHERE last_seen_at < now() - make_interval(days => %s) AND id <> %s)",
                (ACCOUNT_IDLE_DAYS, account),
            ),
            (
                f"DELETE FROM {self.schema}.accounts "
                "WHERE last_seen_at < now() - make_interval(days => %s) AND id <> %s",
                (ACCOUNT_IDLE_DAYS, account),
            ),
            (
                f"INSERT INTO {self.schema}.accounts (id) VALUES (%s) "
                "ON CONFLICT (id) DO UPDATE SET last_seen_at = now() RETURNING blocked",
                (account,),
            ),
            fetch=True,
        )
        return not rows[0][0]

    async def _blocked(self, account: str) -> bool:
        rows = await self._db(
            (f"SELECT 1 FROM {self.schema}.accounts WHERE id = %s AND blocked", (account,)),
            fetch=True,
        )
        return bool(rows)

    async def _discard(self, sign_in_id: str, why: str, status: int) -> Response:
        """End a sign-in that has used up its attempts; the person starts a new one."""
        await self._db((f"DELETE FROM {self.schema}.sign_ins WHERE id = %s", (sign_in_id,)))
        log.warning("Sign-in %s discarded: %s", sign_in_id[:6], why)
        return _page(f"{why} Start again from your app.", status=status)

    async def _grant(self, pending: _Pending, subject: str) -> Response:
        """Issue the authorization code and send the browser back to the client."""
        params = pending.params
        code = secrets.token_urlsafe(32)
        auth_code = AuthorizationCode(
            code=code,
            scopes=params.scopes or [],
            expires_at=time.time() + CODE_TTL,
            client_id=pending.client_id,
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=self.resource,
            subject=subject,
        )
        stored = auth_code.model_copy(update={"code": ""})  # only the hash identifies it
        try:
            await self._db(
                (f"DELETE FROM {self.schema}.codes WHERE expires_at < %s", (time.time(),)),
                # One code per sign-in: a parallel correct submission finds the row gone.
                (
                    f"DELETE FROM {self.schema}.sign_ins WHERE id = %s RETURNING id",
                    (pending.sign_in_id,),
                    True,
                ),
                (
                    f"INSERT INTO {self.schema}.codes (code_hash, data, expires_at) "
                    "VALUES (%s, %s, %s)",
                    (_hash(code), stored.model_dump_json(), auth_code.expires_at),
                ),
            )
        except _Missing:
            return _expired()
        target = construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state)
        return RedirectResponse(target, status_code=302)

    def routes(self) -> list[Route]:
        return [
            Route(SIGN_IN_PATH, self.sign_in_page, methods=["GET"]),
            Route(SIGN_IN_PATH, self.sign_in_submit, methods=["POST"]),
            Route(EMAIL_PATH, self.send_code, methods=["POST"]),
            Route(LOGIN_CODE_PATH, self.check_code, methods=["POST"]),
        ]

    # -- accounts ------------------------------------------------------------

    def account_id(self, address: str) -> str:
        """The account for an email address: a keyed hash, so the stored ID can't
        be turned back into the address, or tested against a guess, without
        MCP_ACCOUNT_SECRET."""
        if not self.account_secret:
            raise ValueError("MCP_ACCOUNT_SECRET is not set.")
        normal = address.strip().lower().encode()
        return hmac.new(self.account_secret.encode(), normal, hashlib.sha256).hexdigest()

    def forget(self, address: str) -> bool:
        """Delete an account, its tokens and its mail log. False if there was none."""
        account = self.account_id(address)
        rows = self._run(
            (f"DELETE FROM {self.schema}.tokens WHERE data->>'subject' = %s", (account,)),
            (f"DELETE FROM {self.schema}.mail_log WHERE account = %s", (account,)),
            (f"DELETE FROM {self.schema}.accounts WHERE id = %s RETURNING id", (account,)),
            fetch=True,
        )
        return bool(rows)

    def block(self, address: str) -> None:
        """Stop an address signing in, and its tokens working, until unblocked by hand."""
        self._run(
            (
                f"INSERT INTO {self.schema}.accounts (id, blocked) VALUES (%s, true) "
                "ON CONFLICT (id) DO UPDATE SET blocked = true",
                (self.account_id(address),),
            )
        )


@dataclasses.dataclass(frozen=True)
class _Pending:
    """A sign-in that has been started and not yet finished."""

    sign_in_id: str
    client_id: str
    client_name: str
    params: AuthorizationParams
    attempts: int
    sends: int

    @property
    def returns_to(self) -> str:
        """The host the browser goes back to; shown so the person can check it."""
        return urlparse(str(self.params.redirect_uri)).hostname or "the app"


def _login_code_hash(sign_in_id: str, code: str) -> str:
    # Bound to the sign-in, so a code is no use against another pending sign-in.
    return _hash(f"{sign_in_id}:{code}")


# -- HTML -----------------------------------------------------------------------

# The sign-in page is only reached from an OAuth flow and is never shared, so it
# opts out of link unfurling (noindex, no Open Graph tags) and of framing.
# No og: or twitter: tags, on purpose: the sign-in page is noindex, lives at a
# one-time address, and is never shared, so it has no link preview to describe.
_HEAD = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Sign in · Traintrackr</title>
<link rel="icon" href="{html.escape(site.asset_url(site.ICON))}" type="image/svg+xml">
<link rel="stylesheet" href="{html.escape(site.asset_url(site.STYLESHEET))}">
</head><body><main class="sign-in">
"""
_FOOT = "</main></body></html>"

_HEADERS = {
    "Cache-Control": "no-store",
    "X-Frame-Options": "DENY",
    # No form-action: browsers apply it to the redirect after the form is posted,
    # and that redirect goes to the client's callback (claude.ai), not 'self'.
    "Content-Security-Policy": "default-src 'none'; style-src 'self'; img-src 'self'",
    "Referrer-Policy": "same-origin",
}


def _page(message: str, status: int = 200) -> HTMLResponse:
    body = f"{_HEAD}<h1>Traintrackr</h1><p>{html.escape(message)}</p>{_FOOT}"
    return HTMLResponse(body, status_code=status, headers=_HEADERS)


def _expired() -> HTMLResponse:
    return _page("This sign-in link has expired. Start again from your app.", status=400)


def _intro(pending: _Pending) -> str:
    return (
        f"{_HEAD}<h1>Sign in to Traintrackr</h1>"
        f"<p><strong>{html.escape(pending.client_name)}</strong> is asking to use Traintrackr. "
        f"After you sign in you go back to <strong>{html.escape(pending.returns_to)}</strong>.</p>"
    )


def _error(error: str) -> str:
    return f'<p class="error" role="alert">{html.escape(error)}</p>' if error else ""


def _hidden(name: str, value: str) -> str:
    return f'<input type="hidden" name="{name}" value="{html.escape(value)}">'


def _form(
    pending: _Pending, *, email: bool, passphrase: bool, error: str = "", status: int = 200
) -> HTMLResponse:
    """The sign-in page: an email field for the public, the passphrase for the owner."""
    email_form = (
        f'<form method="post" action="{EMAIL_PATH}">{_hidden("request", pending.sign_in_id)}'
        '<label for="email">Email address</label>'
        '<input id="email" name="email" type="email" autocomplete="email" required autofocus>'
        f'<p class="hint">We send a {CODE_MINUTES}-minute sign-in code to this address. '
        'See the <a href="/privacy">privacy policy</a>.</p>'
        "<button type=submit>Email me a code</button></form>"
    )
    focus = "" if email else " autofocus"
    passphrase_form = (
        f'<form method="post" action="{SIGN_IN_PATH}">{_hidden("request", pending.sign_in_id)}'
        '<label for="passphrase">Passphrase</label>'
        '<input id="passphrase" name="passphrase" type="password" '
        f'autocomplete="current-password" required{focus}>'
        "<button type=submit>Allow</button></form>"
    )
    if email and passphrase:
        # Most people have no passphrase; keep it out of their way.
        is_open = " open" if "passphrase" in error.lower() else ""
        passphrase_form = (
            f"<details{is_open}><summary>Have a passphrase?</summary>{passphrase_form}</details>"
        )
    body = (
        _intro(pending)
        + _error(error)
        + (email_form if email else "")
        + (passphrase_form if passphrase else "")
        + _FOOT
    )
    return HTMLResponse(body, status_code=status, headers=_HEADERS)


def _code_form(pending: _Pending, address: str, error: str = "", status: int = 200) -> HTMLResponse:
    fields = _hidden("request", pending.sign_in_id) + _hidden("email", address)
    body = (
        _intro(pending)
        + f"<p>We sent a six-digit code to <strong>{html.escape(address)}</strong>. "
        f"It expires in {CODE_MINUTES} minutes.</p>"
        + _error(error)
        + f'<form method="post" action="{LOGIN_CODE_PATH}">{fields}'
        '<label for="code">Sign-in code</label>'
        '<input id="code" name="code" inputmode="numeric" autocomplete="one-time-code" '
        'pattern="[0-9 ]*" maxlength="12" required autofocus>'
        "<button type=submit>Sign in</button></form>"
        f'<form method="post" action="{EMAIL_PATH}">{fields}'
        '<button type=submit class="quiet">Send a new code</button></form>' + _FOOT
    )
    return HTMLResponse(body, status_code=status, headers=_HEADERS)


async def health(_request: Request) -> Response:
    return PlainTextResponse("ok")
