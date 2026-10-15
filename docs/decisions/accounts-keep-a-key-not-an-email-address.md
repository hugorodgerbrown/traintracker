# Accounts keep a key and a masked address, not the email address

Status: accepted (2026-10-15)

## Context

Every account stored its email address twice, as `username` and `email`,
and each pending sign-in stored it again. A copy of any app's database was
a mailing list. Most Titan apps never email anyone except to sign them in,
and that email is sent while the address is in hand. People still need to
see which account they are signed in to.

## Decision

- **A key, not the address.** An account's `username` is `email_key(address)`:
  HMAC-SHA256 of the trimmed, lower-cased address. Signing in again with the
  same address finds the same account. `email` is blank, and a sign-in
  request holds the key, not the address.
- **Its own secret.** The HMAC is keyed with `ACCOUNTS_EMAIL_KEY`, not
  `SECRET_KEY`, so `SECRET_KEY` can be rotated (sessions, signed links)
  without losing every account. titan makes it in the app's env group, and
  the `accounts.E001` check stops `migrate`, so a deploy, without it.
- **A masked address.** `masked_email` (`h•••@example.com`: the first letter
  and the domain) is kept on the user, and on the request so the account made
  from it has it. Pages and passkeys show it.
- **A custom user model.** `accounts.User` (`AbstractUser` plus
  `masked_email`), rather than a profile beside `auth.User`. Django advises
  one from the start, and swapping one in later can't be done by a
  migration. No Titan app had live data when this landed, so each reset its
  database once.
- **An override.** `ACCOUNTS_KEEP_EMAIL = True` (in settings, not the
  environment: it decides what the privacy notice says) keeps the address
  as the username and email, for an app that has to email people later.
  `masked_email` is still set, so templates don't change.
- **The privacy notice follows the setting**, on the page and through the
  MCP tool, so it says what the app's code stores.

## Consequences

- **Never change `ACCOUNTS_EMAIL_KEY`.** Changing or losing it gives every
  address a key no account has: everyone signs up afresh, and their data is
  stranded. Keep a copy outside Render.
- Without the address an app can't email anyone except while they sign in:
  no digests, alerts or account notices. Such an app sets
  `ACCOUNTS_KEEP_EMAIL`, and its notice says so.
- Staff find an account by typing its whole address into the admin's user
  search, which turns it into the username; the list shows masked
  addresses.
- `createsuperuser` stores the address it is given. The first sign-in with
  that address finds the account by it, then keys it and blanks the address.
- Turning `ACCOUNTS_KEEP_EMAIL` on or off later keeps every account: each
  is found by its old username at its next sign-in and stored the new way.
  Turning it on stores addresses only as people sign in again.
- Reviewer accounts keep their address as it is: a test address with no one
  behind it, which staff read in the admin.
- The address is a task argument while the sign-in email is sent. With the
  thread backend it stays in memory; a queue-backed backend would store it,
  and the privacy notice would have to say so.
