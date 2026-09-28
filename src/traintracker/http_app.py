"""Streamable-HTTP entry point for hosting the server (e.g. on Render).

/mcp needs a bearer token: either the static MCP_AUTH_TOKEN, or an OAuth access
token issued by this server after the passphrase sign-in (see oauth.py), which
is what lets claude.ai add the server as a connector. The OAuth metadata,
registration, authorize, token and revoke endpoints come from the MCP SDK.
/healthz is open for the platform's health check.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mcp.server.auth.provider import ProviderTokenVerifier
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.transport_security import TransportSecuritySettings
from starlette.routing import Route

from traintracker.oauth import TraintrackerOAuthProvider, health

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer
    from starlette.applications import Starlette

    from traintracker.config import Settings

HEALTH_PATH = "/healthz"


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


def build_app(server: MCPServer[Any], settings: Settings) -> Starlette:
    provider = TraintrackerOAuthProvider(
        dsn=settings.database_url or "",
        public_url=settings.public_url,
        passphrase=settings.oauth_passphrase,
        static_token=settings.mcp_auth_token,
        schema=settings.auth_schema,
    )
    provider.create_tables()
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
    return server._lowlevel_server.streamable_http_app(
        host=settings.host,
        transport_security=transport_security(list(settings.public_hosts)),
        auth=auth,
        auth_server_provider=provider,
        token_verifier=ProviderTokenVerifier(provider),
        custom_starlette_routes=[*provider.routes(), Route(HEALTH_PATH, health)],
    )


def serve(app: Starlette, host: str, port: int) -> None:
    import uvicorn

    uvicorn.run(app, host=host, port=port, proxy_headers=True)
