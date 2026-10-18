# Saved boards live in the browser or the account, never both

Status: accepted (2026-10-18)

## Context

The front page always showed the same example board. People who come back
want the board they used last, or one of a few they use often (TRA-37, with
mockups). Most people who open boards never sign in: signing in exists to
connect Claude or ChatGPT. Those who do sign in may use several devices. The
front page is publicly cached for five minutes, and the site has kept nothing
about signed-out visitors so far.

## Decision

- **Signed out, the browser keeps them; signed in, the account does.**
  `localStorage` when `/api/boards` says `signed_in: false`, the `SavedBoard`
  rows when it says true. Considered: browser only (stuck on one device),
  account only (misses most people who use boards), and both kept in sync
  (two copies to reconcile for no gain).
- **Signing in moves the browser's boards into the account, once.** Its
  favourites join the account's while there is room (they never push one
  out), its last board only if the account has none, and the browser copy is
  then cleared. So nothing is lost, nothing is kept twice, and signing out
  leaves nothing behind on a shared computer.
- **Board addresses only.** A saved board is its one address
  (`board.from_path`), at most 12 favourites and one last board an account;
  rows that are neither are deleted as soon as they are not needed.
- **The front page's HTML stays the same for everyone.** `saved_boards.js`
  and `site.js` draw the visitor's boards in the browser, so the page keeps
  its public cache and a signed-in person's boards are never cached for
  anyone else. The last board is drawn from its JSON feed into
  `#your-board`, the same live board the board page has; if it can't be
  fetched the example stays.
- **Only a board's own page is remembered.** Embeds run on other people's
  sites and TV views are screens, so neither loads the script.
- **`GET /api/boards` answers a signed-out visitor with 200, not 401.**
  Every signed-out visit to the front page or a board asks it, and a 401
  would show as an error in the browser's console. The writes stay behind
  `login_required_json`.

## Consequences

- Signed-out favourites stay on one browser and go when its site data is
  cleared.
- A front page or board view costs one small request to `/api/boards`
  (no database query when signed out), and a signed-in board view one write.
- The first JavaScript tests arrive with this (Vitest, `tests/js/`), with a
  `js` tox env and CI job, and `npm audit` in `audit`.
