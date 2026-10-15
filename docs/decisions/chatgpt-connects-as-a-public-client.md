# ChatGPT connects as a public client

Status: accepted (2026-10-15)

## Context

ChatGPT identifies itself with a client metadata document (CIMD):
`client_id=https://chatgpt.com/oauth/client.json`. The document prefers
`private_key_jwt` (`token_endpoint_auth_method`) and lists both `none` and
`private_key_jwt` in `token_endpoint_auth_methods_supported`, with a
`jwks_uri`. OpenAI's docs say ChatGPT picks a method from that list and the
ones the server advertises.

django-oauth-toolkit 3.4.1, the latest release, reads only the preference and
refuses any method but `none`, so the authorize page answered "Invalid
client_id parameter value" (production log, 15 October 2026:
`CIMD clients must be public; got token_endpoint_auth_method 'private_key_jwt'`).
Upstream tracks it as django-oauth-toolkit issue 1845.

## Decision

- **A CIMD client that lists `none` is registered as public**
  (`apps/mcp/cimd.py`, wired in `McpConfig.ready`). Everything else the toolkit
  checks still applies: no `client_secret`, a non-empty `redirect_uris` held to
  `MCP_AUTH["REDIRECT_URI_PATTERNS"]` at consent, one grant, PKCE required.
- **A client that does not list `none` is still refused.** We don't verify
  client assertions, so we never accept one in place of PKCE.
- **We don't advertise `private_key_jwt`**, so ChatGPT's choice from the two
  lists is `none`, the same footing Claude connects on.

Verifying `private_key_jwt` against ChatGPT's JWKS was the other way. It needs
a JWKS fetch with the same SSRF hardening as the CIMD fetch, and the toolkit
doesn't do it yet, so it buys ChatGPT nothing it can't do as a public client.

## Consequences

- ChatGPT's connection is as strong as Claude's: PKCE, an allowlisted callback,
  rotating refresh tokens. It isn't tied to ChatGPT's signing key.
- The fix wraps a private toolkit function. Remove it once the toolkit takes
  `none` from the list, or move it into mcp-auth beside its grant-types fix so
  every Titan app gets it.
