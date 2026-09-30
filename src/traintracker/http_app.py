"""Streamable-HTTP entry point for hosting the server (e.g. on Render).

/mcp needs a bearer token: either the static MCP_AUTH_TOKEN, or an OAuth access
token issued by this server after sign-in (see oauth.py), which is what lets
claude.ai and ChatGPT add the server as a connector. The OAuth metadata,
registration, authorize, token and revoke endpoints come from the MCP SDK.
Tool calls are rate limited per account (see ratelimit.py). The endpoints that
come before sign-in have no account to count against, so they are limited per
client address (OpenEndpointLimits).
/healthz is open for the platform's health check, and so are the public pages
(see site/): /, /docs, /privacy and /terms. With OPENAI_APPS_CHALLENGE set,
/.well-known/openai-apps-challenge returns it, for OpenAI's domain check.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from mcp.server.auth.handlers.metadata import MetadataHandler
from mcp.server.auth.provider import ProviderTokenVerifier
from mcp.server.auth.routes import (
    AUTHORIZATION_PATH,
    REGISTRATION_PATH,
    REVOCATION_PATH,
    TOKEN_PATH,
    build_metadata,
    cors_middleware,
)
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.transport_security import RequestBodyLimitMiddleware, TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from traintracker import site
from traintracker.mail import build_mailer
from traintracker.oauth import (
    EMAIL_PATH,
    LOGIN_CODE_PATH,
    OFFLINE_ACCESS,
    SIGN_IN_PATH,
    TraintrackerOAuthProvider,
    health,
)
from traintracker.ratelimit import PLAN_COST, RateLimiter, RateLimitMiddleware, limited

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer
    from starlette.applications import Starlette
    from starlette.types import ASGIApp, Receive, Scope, Send

    from traintracker.config import Settings

HEALTH_PATH = "/healthz"
METADATA_PATH = "/.well-known/oauth-authorization-server"
OPENAI_CHALLENGE_PATH = "/.well-known/openai-apps-challenge"
# A tool call is a few hundred bytes. The SDK's default of 4 MiB lets one request
# hand the server megabytes of text to parse.
MAX_MCP_BODY = 64 * 1024
# The OAuth and sign-in endpoints take a few form fields or a short JSON object.
MAX_OPEN_BODY = 16 * 1024
OPEN_PATHS = frozenset(
    {
        REGISTRATION_PATH,
        AUTHORIZATION_PATH,
        TOKEN_PATH,
        REVOCATION_PATH,
        SIGN_IN_PATH,
        EMAIL_PATH,
        LOGIN_CODE_PATH,
    }
)
# What one client address may do before anyone has signed in: (at once, a minute).
# Each of these writes a row. Registration is called by the assistant's own
# servers, a few addresses for all of its users, so it is given more room.
REGISTER_LIMIT = (30, 20.0)
AUTHORIZE_LIMIT = (20, 10.0)
# Asking for a sign-in code sends an email to whatever address was typed: five
# at once, then ten an hour.
EMAIL_LIMIT = (5, 10 / 60)


class OpenEndpointLimits:
    """ASGI middleware for the endpoints anyone can call before signing in.

    They take small bodies only, and a client address may call each at a
    limited rate; over it the answer is 429 with Retry-After. `limits` maps
    (method, path) to a limiter, and entries that share a limiter share its
    count. `ip_header` names the header the hosting platform puts the caller's
    address in; without it the connection's own address is used.
    """

    def __init__(
        self,
        app: ASGIApp,
        limits: Mapping[tuple[str, str], RateLimiter],
        ip_header: str | None = None,
    ) -> None:
        self.app = app
        self.small = RequestBodyLimitMiddleware(app, MAX_OPEN_BODY)
        self.limits = limits
        self.ip_header = ip_header.lower().encode() if ip_header else None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] not in OPEN_PATHS:
            await self.app(scope, receive, send)
            return
        limiter = self.limits.get((scope["method"], scope["path"]))
        if limiter and (wait := limiter.take(self.address(scope))):
            refused = PlainTextResponse(
                limited(wait), status_code=429, headers={"Retry-After": str(wait)}
            )
            await refused(scope, receive, send)
            return
        await self.small(scope, receive, send)

    def address(self, scope: Scope) -> str:
        if self.ip_header:
            for name, value in scope["headers"]:
                if name == self.ip_header:
                    return str(value.decode("latin-1").split(",")[0].strip())
        client = scope.get("client")
        return str(client[0]) if client else "unknown"


def open_endpoint_limits() -> dict[tuple[str, str], RateLimiter]:
    register = RateLimiter(REGISTER_LIMIT[1], REGISTER_LIMIT[0])
    authorize = RateLimiter(AUTHORIZE_LIMIT[1], AUTHORIZE_LIMIT[0])
    return {
        ("POST", REGISTRATION_PATH): register,
        ("GET", AUTHORIZATION_PATH): authorize,
        ("POST", AUTHORIZATION_PATH): authorize,
        ("POST", EMAIL_PATH): RateLimiter(EMAIL_LIMIT[1], EMAIL_LIMIT[0]),
    }


def transport_security(public_hosts: list[str]) -> TransportSecuritySettings | None:
    """DNS-rebinding protection for the hostnames the server is reached on.

    The bind address (HOST, 0.0.0.0 on Render) says nothing about the Host header
    clients send, so the public names are configured separately. With none, the
    SDK's default applies (checks only for localhost binds).
    """
    if not public_hosts:
        return None
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[p for h in public_hosts for p in (h, f"{h}:*")],
        allowed_origins=[f"https://{h}" for h in public_hosts],
    )


def build_provider(settings: Settings) -> TraintrackerOAuthProvider:
    return TraintrackerOAuthProvider(
        dsn=settings.database_url or "",
        public_url=settings.public_url,
        passphrase=settings.oauth_passphrase,
        static_token=settings.mcp_auth_token,
        schema=settings.auth_schema,
        account_secret=settings.account_secret,
        mailer=build_mailer(settings),
        mail_max_per_hour=settings.mail_max_per_hour,
    )


def limit_tool_calls(server: MCPServer[Any], settings: Settings) -> None:
    """Put the rate limiter on the server, in place of any from an earlier app.

    The server object outlives the app (tests build many apps around one
    server), so the old limiter is removed rather than stacked.
    """
    chain = server.middleware
    chain[:] = [m for m in chain if not isinstance(m, RateLimitMiddleware)]
    if settings.rate_limit_per_minute > 0:
        limiter = RateLimiter(settings.rate_limit_per_minute, settings.rate_limit_burst)
        chain.append(RateLimitMiddleware(limiter))


def site_fields(settings: Settings) -> dict[str, str]:
    """This server's own values for the public pages."""
    if settings.rate_limit_per_minute > 0:
        fair_use = (
            f"Each account can make {max(1, settings.rate_limit_burst)} requests at once and "
            f"{settings.rate_limit_per_minute} a minute; a journey plan counts as {PLAN_COST}. "
            "Over that, the assistant is told how long to wait."
        )
    else:
        fair_use = "There is no set limit on requests; please don't automate them."
    return {
        "site_url": settings.public_url,
        "mcp_url": f"{settings.public_url}/mcp",
        "fair_use": fair_use,
    }


def openai_challenge(token: str | None) -> list[Route]:
    """The token OpenAI's plugin portal issues to prove this server's domain,
    returned as the bare text it expects. No route at all until one is set."""
    if not token:
        return []

    async def endpoint(_request: Request) -> PlainTextResponse:
        return PlainTextResponse(token)

    return [Route(OPENAI_CHALLENGE_PATH, endpoint)]


def build_app(server: MCPServer[Any], settings: Settings) -> Starlette:
    provider = build_provider(settings)
    provider.create_tables()
    limit_tool_calls(server, settings)
    auth = AuthSettings(
        issuer_url=settings.public_url,
        resource_server_url=provider.resource,
        client_registration_options=ClientRegistrationOptions(enabled=True),
        revocation_options=RevocationOptions(enabled=True),
        validate_token_resource=True,
    )
    # MCPServer.streamable_http_app takes its auth from constructor settings; the
    # module-level server is built before configuration is read, so pass the
    # auth pieces to the low-level app directly.
    app = server._lowlevel_server.streamable_http_app(
        host=settings.host,
        max_request_body_size=MAX_MCP_BODY,
        transport_security=transport_security(list(settings.public_hosts)),
        auth=auth,
        auth_server_provider=provider,
        token_verifier=ProviderTokenVerifier(provider),
        custom_starlette_routes=[
            *provider.routes(),
            *site.routes(site_fields(settings)),
            Route(HEALTH_PATH, health),
            *openai_challenge(settings.openai_apps_challenge),
        ],
    )
    advertise_refresh_tokens(app, auth)
    app.add_middleware(
        OpenEndpointLimits, limits=open_endpoint_limits(), ip_header=settings.client_ip_header
    )
    return app


def advertise_refresh_tokens(app: Starlette, auth: AuthSettings) -> None:
    """Replace the SDK's authorization server metadata with one that lists
    `offline_access` and public clients.

    ChatGPT may drop a connection when its access token expires unless the
    metadata lists `offline_access`, and Claude asks for that scope when it is
    listed. The server issues a refresh token on every sign-in either way. The
    SDK takes `scopes_supported` from the registration options' `valid_scopes`,
    which would also make registration refuse any other scope a client asks
    for, and it never lists `none`, though registration accepts public clients.
    So the metadata is built here and swapped in; the provider accepts
    `offline_access` from every client (see `TraintrackerOAuthProvider.get_client`).
    """
    metadata = build_metadata(
        auth.issuer_url,
        auth.service_documentation_url,
        auth.client_registration_options or ClientRegistrationOptions(),
        auth.revocation_options or RevocationOptions(),
    )
    metadata.scopes_supported = [OFFLINE_ACCESS]
    metadata.token_endpoint_auth_methods_supported = [
        *(metadata.token_endpoint_auth_methods_supported or []),
        "none",
    ]
    endpoint = cors_middleware(MetadataHandler(metadata).handle, ["GET", "OPTIONS"])
    routes = app.router.routes
    for i, route in enumerate(routes):
        if isinstance(route, Route) and route.path == METADATA_PATH:
            routes[i] = Route(METADATA_PATH, endpoint=endpoint, methods=["GET", "OPTIONS"])
            return
    raise RuntimeError(f"The MCP SDK no longer serves {METADATA_PATH}; update this function.")


def serve(app: Starlette, host: str, port: int) -> None:
    import uvicorn

    uvicorn.run(app, host=host, port=port, proxy_headers=True)
