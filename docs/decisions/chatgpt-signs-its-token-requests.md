# ChatGPT signs its token requests

Status: accepted (2026-10-15)

## Context

ChatGPT identifies itself with a client metadata document (CIMD):
`client_id=https://chatgpt.com/oauth/client.json`. The document declares
`token_endpoint_auth_method: private_key_jwt` and publishes its public keys at
`jwks_uri`. django-oauth-toolkit 3.4.1, the latest release, refuses any method
but `none`, so the authorize page answered "Invalid client_id parameter value"
(production log, 15 October 2026:
`CIMD clients must be public; got token_endpoint_auth_method 'private_key_jwt'`;
upstream, django-oauth-toolkit issue 1845).

ChatGPT also lists `none` among the methods it can use, so registering it as a
public client would have been enough to connect. The CIMD draft (section 8.2)
says otherwise: a client that declares `private_key_jwt` is confidential, and
the server MUST require its signed assertion.

## Decision

- **Verify ChatGPT's key, in mcp-auth v0.1.3**, so every Titan app gets it. A
  `private_key_jwt` document makes a confidential client. The token and
  revocation endpoints accept it only with an RFC 7523 assertion signed by a key
  from its document, as well as PKCE, and each assertion is used once.
- **Not a public-client downgrade.** That would have connected ChatGPT with the
  same protection as Claude, but a stolen ChatGPT refresh token could then be
  used without ChatGPT's key.

## Consequences

- mcp-auth's contract suite runs ChatGPT's whole connection, so a settings
  change that breaks it fails this project's build.
- The token endpoint fetches ChatGPT's keys (cached for the document's max-age)
  through the toolkit's SSRF-hardened fetcher.
