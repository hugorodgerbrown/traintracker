# Accounts keep a key, not an email address

Status: accepted (2026-10-14)

## Context

Signing in by email needs the address for one moment: to send the link and
code. After that, Traintrackr only needs to recognise the same person next
time (to find their account, passkeys and fair-use bucket). Storing the
address itself made it the most sensitive thing in the database, for no use:
Traintrackr sends no email except the sign-in one.

## Decision

Store `email_key(address)` instead of the address: HMAC-SHA256 of the
trimmed, lower-cased address, keyed with `SECRET_KEY` (salt
`accounts.email`). It is the account's `username` (its `email` is blank) and
the sign-in request's `email_key`. The address lives only in memory while the
email is sent; the session keeps a masked hint ("w•••@example.com") so the
code page can still say where the email went.

A keyed hash, not a plain one: a plain SHA-256 of an address can be reversed
by hashing a list of known addresses. Without the secret key, a copy of the
database can't be checked against guesses at all.

Not a fresh, unlinked account per sign-in: that would end per-account limits,
passkeys and "delete my data", since the same person would never be found
again.

## Consequences

- Traintrackr can't email its users (no notices, no breach letters) except
  in reply to them; the privacy policy says so.
- The account page can't say who is signed in; the passkey's name in a
  device's password manager is "Traintrackr account".
- The admin lists accounts by key. To find someone's, compute their key.
- Rotating `SECRET_KEY` changes every key and orphans every account and
  passkey. Don't rotate it; if it must change, rewrite the usernames with
  the old and new keys in one migration.
- The key is still personal data (pseudonymised) under the UK GDPR, so the
  privacy policy still lists it and `forget_account` still deletes it.
- A queue-backed task backend would write the address to its queue; clear
  task arguments once sent if one is adopted (`apps/accounts/tasks.py`).
- Migration `accounts.0003` keys every existing account and request; the
  addresses can't be brought back.
- A `ReviewerAccount` keeps its address as it is, on purpose: it is a test
  address with no mailbox and no person behind it, and staff read it in the
  admin. Its pending sign-ins are still found by key
  (`SignInRequest.objects.for_email`), and the account it signs in to is
  keyed like any other.
