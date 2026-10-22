# The 500 page renders without the request

Status: accepted (2026-10-21)

## Context

Django's default error pages are bare ("Not Found", "Server Error (500)"),
with no way back to the site. Rendering ours on `public_base.html` like any
other page is the obvious fix, but that template runs the context
processors: `request.user` (the session, then the database), the PWA
identity, fivebar's tag. A 500 is often caused by exactly those: the
database is down, a migration hasn't run, the session table is locked. An
error page that needs the thing that just failed raises inside the handler,
and Django falls back to its bare page anyway.

A 400 is the same: `DisallowedHost` is a 400, and then every call to
`request.get_host()` raises again.

## Decision

The 400 and 500 pages render `templates/errors/standalone.html` with
`render_to_string` and no request, so no context processor runs. Their
context is the error and the app's identity from `apps/pwa/conf.py`, read
from config. The page draws the same header (without the Sign in link) and
the same footer, from the same CSS. If that render fails too (a missing
static manifest), the handler sends `FALLBACK_HTML`, a literal string.

403, the CSRF failure and 404 render `errors/page.html` on
`site_base.html` (Traintrackr's header, footer and `site.css`) with the request: the site is working, the reader keeps
their signed-in header. If that render fails, they drop to the standalone
page.

Every handler also answers JSON (under `/mcp` and `/.well-known/`, or when
the client accepts JSON but not HTML) and plain text to htmx, and varies on
`Accept` and `HX-Request`.

## Consequences

- The 500 page has no signed-in state. Anything added to it must come from
  a plain dict. fivebar's tag is the one exception that reads the request
  (2026-10-22, TRA-38): `analytics(request)` reads only settings and
  `request.resolver_match`, never the session, the user or the host, so it
  can't fail the way the page's cause did.
- The standalone page copies `public/_header.html` by hand, without its
  signed-in link, and includes `public/_footer.html` with the links from
  `apps/public/views.py: NAV`. Change them together.
- The CSRF failure keeps `X-CSRF-Failure: 1` in every shape, so the outbox
  still pauses a queued write instead of dropping it (writes-go-through-the-outbox.md).
- With `DEBUG` on, Django shows its technical pages. Traintrackr has no
  design page to preview these from; run with `DEBUG` off to see one.
