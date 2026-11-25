# People delete their own account, for good

Status: accepted (2026-11-08)

## Context

Titan apps launch open to anyone, as one-week experiments with no
guarantees: no backups and no error monitoring (Linear RAS-36, 7 Oct 2026).
The privacy notice said "you can ask for your account to be deleted", which
needs someone to read the request, find the account (we don't keep the
address) and delete it by hand. With no backups, a deletion also can't be
undone by restoring one.

## Decision

The account page has a **Delete my account** action with one confirmation
page, and the delete happens at once. One function,
`apps/accounts/deletion.py: delete_account`, removes the user, every row
that cascades from it (including MCP tokens and grants) and its sign-in
requests, in one transaction. Apps reach data the cascade can't through
`ACCOUNTS_DELETE_HOOKS`; most need none. The confirmation is a second
button rather than typing the address, because accounts keep only a key
and a masked address, so there is nothing to compare a typed address with
that the person can see.

## Consequences

- Nobody has to handle deletion requests, and the privacy notice tells
  people how to do it themselves and that it is final.
- There is no grace period and no undo. A mistaken deletion loses
  everything; the confirmation page says so before the button.
- fivebar's daily totals stay: they hold nothing that identifies the person.
- An app that stores per-account data without a foreign key to the user
  must add a hook, or that data outlives the account.
