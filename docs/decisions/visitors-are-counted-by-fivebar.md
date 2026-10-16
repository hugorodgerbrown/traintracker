# Visitors are counted by fivebar, and only once the app is live

Status: accepted (2026-10-12)

## Context

Every Titan app needs to know how many people use it and which pages they
open, without a cookie banner and without weakening a CSP that allows
nothing but this origin. fivebar (https://fiveb.ar) counts visits with one
async script tag and no cookies; Traintrackr already uses it. It has no
package and no server code; its API can add a site.

## Decision

- The tag goes in `templates/includes/_head.html`, so every page, public or
  in the app, carries it once. It loads from fiveb.ar as written, never
  bundled or precached: the script sends its counts to the host it came
  from.
- It is off unless `FIVEBAR_DOMAIN` is set. Locally, in CI and in tests
  nothing is drawn and the CSP gains nothing. titan sets it at go-live to
  the app's domain, next to `SITE_URL`, and adds the site to fivebar
  through its API (by hand at https://fiveb.ar/sites when titan has no
  `FIVEBAR_API_KEY`).
- While it is on, `https://fiveb.ar` is added to `script-src` and
  `connect-src` only.
- A page whose route takes values (an emailed sign-in link's token, a
  note's uuid) is counted under its route, `data-path="/signin/link/:token/"`,
  worked out from the URL resolver, so no secret or id reaches fivebar and
  a new route needs nothing added.
- An event is marked by the server with a fragment on a redirect, read by
  a static script, never by an inline script (the CSP forbids them) or a
  flag in the session (a page cached by the service worker would replay it).
- The privacy notice names fivebar, and what it receives, only while it is
  on.

## Consequences

- An app counts nothing until `titan golive`, and nothing before the site
  is added in fivebar.
- Offline, the script and its counts fail quietly; the service worker
  leaves other origins to the browser.
- One event ships: "Sign up", an account's first sign-in, so a new app can
  check it is counting. The sign-in redirect ends in `#signed-up`, which
  `static/js/analytics.js` sends and takes out of the address, so a reload
  or a copied link doesn't count again. More events are each app's to add:
  a fragment in `EVENTS` (both `apps/core/analytics.py` and the script), or
  `data-tally` on anything clicked; see https://fiveb.ar/docs/events.
- Error responses are counted as ordinary pages; Safari needs
  `data-status` on the tag to tell them apart, which the template doesn't
  set.

## MCP tool calls (2026-10-16, RAS-53)

An MCP client calls from its own servers, so the tag never sees the MCP
endpoint. fivebar's server events (https://fiveb.ar/docs/server-events)
count it instead: `apps/mcp/events.py` sends one event per `tools/call`.

- Named after the tool under `/mcp/`, `/mcp/add_note` (RAS-54), so it
  lands in Top pages beside the site's pages but can't be taken for one,
  with `props.outcome` `ok` or `error` (a `ToolError`). An unknown tool
  name or bad arguments send nothing.
- Off unless `FIVEBAR_TOKEN` is set as well as `FIVEBAR_DOMAIN`: a token
  made for the one site with **Send events** and not **Read stats**, so a
  leaked one reads nothing. It is made by hand; titan doesn't mint it.
- Sent from a two-thread pool with a 2-second timeout, never on the
  request: a slow or failing fivebar is logged and dropped, and the call
  answers as fast as before. Not a django-tasks job, which would write a
  row per call in production and run inline in development.
- `id` is a keyed hash of the user's id (`salted_hmac` with the app's
  secret), so fivebar tells accounts apart without ever holding one.
  `client` is the connected OAuth app's name, lower-cased, so Claude's
  calls sit under AI Assistants. No IP or user agent: they would place the
  client's servers, not the person. No arguments, results or anything the
  person wrote.
- Calls count towards fivebar's billable pageviews, like pages.

## In Traintrackr (2026-10-16, TRA-32)

Copied from the template, which Traintrackr's site predates. The site's
own tag is in `apps/public/templates/public/_head.html`, and the sign-in
pages never load it. Every tool is a train tool, so the events read
`/mcp/live_departures`, `/mcp/plan_journey` and so on. A call refused by
the fair-use limit is a `ToolError`, so it counts with outcome `error`; a
journey plan counts once, though the limit charges it three. `serve_stdio`
has no account and sends nothing. The privacy page states the tool-call
row whether or not `FIVEBAR_TOKEN` is set, because the `privacy_policy`
tool reads the page's own text: it describes the deployment at
traintrackr.live, where the token is set.
