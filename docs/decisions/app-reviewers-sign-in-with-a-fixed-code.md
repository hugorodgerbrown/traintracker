# App reviewers sign in with a fixed code

Status: accepted (2026-10-15)

## Context

OpenAI's app review needs a demo account its reviewers can sign in to with
no further set-up and no MFA, SMS, email or other verification. Anthropic's
directory asks the same. Titan apps have no passwords: sign-in is an
emailed link or code, or a passkey, and a passkey can't be handed over.

A shared reviewer mailbox is still email verification. A pre-issued OAuth
token has nowhere to go: reviewers connect through the normal OAuth flow in
ChatGPT or Claude and land on the sign-in page. A password for one account
would add a second form and a second way in.

## Decision

A `ReviewerAccount` row, managed in the admin, holds an address and a
six-digit code made on save. That address is sent no email and its sign-in
request takes the fixed code, through the existing email and code pages.
The row doesn't link to a user: the account is made on first sign-in like
any other.

## Consequences

- One new model and no new page. The existing limits hold: five wrong codes
  per request, five requests an hour per address.
- The code is kept unhashed so staff can read it, an exception to the
  hashes-only rule for sign-in secrets. It is a shared test credential for
  an account with no one's data in it; the admin action replaces it after a
  review.
- Every app gets it with `copier update`; with no rows, nothing changes.
