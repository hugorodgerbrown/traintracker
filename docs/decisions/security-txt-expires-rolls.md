# security.txt's Expires rolls with the date

Status: accepted (2026-10-23)

## Context

RFC 9116 requires an `Expires` field in `/.well-known/security.txt`, and
recommends it be less than a year ahead, so a file nobody maintains stops
being trusted. A fixed date written into the template would expire on the
same day in every app, and each app would serve an expired file until a
`copier update` with a new date reached it; a test pinned to that date
would fail every app's CI on the same morning, on PRs with nothing to do
with it.

## Decision

`apps.core.views.security_txt` builds the file per request. `Expires` is
always 180 days from now, `Contact` is the `SECURITY_CONTACT` setting
(`mailto:sec@ultraniche.ai` unless the environment says otherwise) and
`Canonical` is `SITE_URL` plus the path.

## Consequences

- The file can't go stale by date, so `Expires` no longer signals that
  nobody is reading the inbox. Keeping `sec@ultraniche.ai` monitored is
  what keeps the file true; if an app's reports should go elsewhere, set
  `SECURITY_CONTACT` in its env group.
- No PGP key and no policy page: add `Encryption` or `Policy` lines to the
  view when there is one to link.
