# Directory reviewers sign in with a fixed code

Status: accepted (2026-10-15)

## Context

OpenAI's app review needs a demo account its reviewers can sign in to with
no further set-up, and with no MFA, SMS, email or other verification.
Anthropic's directory asks the same. Traintrackr has no passwords: sign-in
is an emailed link or code, or a passkey, and a passkey can't be handed over.

The options were a shared reviewer mailbox (still email verification, so
refused), a pre-issued OAuth token (the reviewers connect through the normal
OAuth flow in ChatGPT and Claude, so there is nowhere to give it), a
password for one account (a new form and a second way in), or a fixed code
for one allow-listed address.

## Decision

One address, `REVIEWER_EMAIL`, signs in with `REVIEWER_CODE`, six fixed
digits, through the existing email and code pages. It is sent no email.
Both are environment secrets; unset, the feature is off.

## Consequences

- No new page, model or migration. The code is hashed and limited like any
  other, and the address is only compared in memory, so it fits accounts
  that keep a key rather than an address.
- The fixed code doesn't expire on its own: it should be changed after each
  review. Brute force is held to 25 guesses an hour by the existing limits,
  for an account that can only read public rail data.
