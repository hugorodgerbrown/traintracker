"""Streamable-HTTP entry point for hosting the server (e.g. on Render).

/mcp needs a bearer token: either the static MCP_AUTH_TOKEN, or an OAuth access
token issued by this server after sign-in (see oauth.py), which is what lets
claude.ai and ChatGPT add the server as a connector. The OAuth metadata,
registration, authorize, token and revoke endpoints come from the MCP SDK.
Tool calls are rate limited per account (see ratelimit.py).
/healthz is open for the platform's health check, and so are the public pages
(see site/): /, /docs, /privacy and /terms.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mcp.server.auth.handlers.metadata import MetadataHandler
from mcp.server.auth.provider import ProviderTokenVerifier
from mcp.server.auth.routes import build_metadata, cors_middleware
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.transport_security import TransportSecuritySettings
from starlette.routing import Route

from traintracker import site
from traintracker.mail import build_mailer
from traintracker.oauth import OFFLINE_ACCESS, TraintrackerOAuthProvider, health
from traintracker.ratelimit import RateLimiter, RateLimitMiddleware

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer
    from starlette.applications import Starlette

    from traintracker.config import Settings

HEALTH_PATH = "/healthz"
METADATA_PATH = "/.well-known/oauth-authorization-server"


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
            f"{settings.rate_limit_per_minute} a minute. Over that, the assistant is told how "
            "long to wait."
        )
    else:
        fair_use = "There is no set limit on requests; please don't automate them."
    return {
        "site_url": settings.public_url,
        "mcp_url": f"{settings.public_url}/mcp",
        "fair_use": fair_use,
    }


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
        transport_security=transport_security(list(settings.public_hosts)),
        auth=auth,
        auth_server_provider=provider,
        token_verifier=ProviderTokenVerifier(provider),
        custom_starlette_routes=[
            *provider.routes(),
            *site.routes(site_fields(settings)),
            Route(HEALTH_PATH, health),
        ],
    )
    advertise_refresh_tokens(app, auth)
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
