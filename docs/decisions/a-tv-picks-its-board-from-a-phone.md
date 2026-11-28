# A TV picks its board from a phone, with no sign-in

Status: accepted (2026-11-28)

## Context

People want Traintrackr as an app on their Google TV or Android TV, as
Riffle has (TRA-42, modelled on Riffle's RAS-67 and RAS-68). The board's TV
view, `/board/<station>[/<then>]?tv=1`, already fills a screen, keeps it
awake and reloads itself nightly. What a TV's browser does badly is
everything around it: opening from the home screen, recovering by itself,
and being given an address without typing it with a remote.

Unlike a Riffle display, a Traintrackr board is public: its address is not a
secret. So the TV doesn't need to be trusted with anything; it only needs to
know which board to show.

## Decision

- **The TV app is a shell around the board.** `android-tv/` is a Kotlin
  app with one activity and one library (ZXing, for the QR code) that shows
  the board's `?tv=1` view in a full-screen WebView. Every board change
  reaches TVs with no app update. It follows only links on our own server,
  and only takes a board's TV address (`/board/LST/COL?tv=1`) from a
  pairing. Redrawing the board natively was ruled out (two boards drift), and
  a Trusted Web Activity too (Google TV has no Chrome to host one).
- **The board is chosen on a phone, with a short code.** The TV starts a
  pairing (`POST /tv/pair`) and shows a six-character code, drawn as flaps,
  and a QR code. On a phone, `/tv` takes the code (the QR code fills it in)
  and a board: the person's last board and favourites, which
  `saved_boards.js` adds from the browser or the account, the board whose
  page they came from, or a station typed. The TV polls `/tv/pair/<token>`
  every three seconds and is handed the board's TV address. This is the
  device flow Riffle, YouTube and Netflix use. Considered: searching for a
  station with the remote (slow to type), and a Google Cast receiver (needs a
  registered Cast app and works only from Chrome and Android, not from
  Safari on an iPhone).
- **Nobody signs in.** Boards are public and most people who use them never
  sign in, so the phone's page asks for none, and claims are rate limited
  per address rather than per account.

## Consequences

- Only hashes are stored: the poll token's sha256 and the code's HMAC with
  the server's secret, with the board's address once claimed. Codes last ten
  minutes and work once; the TV can collect its board again for ten minutes
  after the claim, so a reply lost on a flaky network doesn't strand it.
  `purge_tv_pairings` clears expired rows with the other daily purges.
- Starting a pairing is CSRF-exempt: the TV sends no cookies, so there is no
  session for a forged request to use, and all it makes is a code that soon
  expires. Starts are limited to 20 an hour per address, claims to 10 in ten
  minutes per address.
- A guessed or leaked code can put a public departure board on a stranger's
  TV for a moment, and nothing else: no account, no person and no private
  page is ever linked to a TV.
- The app is sideloaded from a GitHub release (docs/android-tv.md); a Google
  Play listing is a later decision. Old Chromecasts without Google TV can't
  run apps, and still show a board by casting a Chrome tab of the TV link.
- The TV doesn't remember boards as a person's last board: TV views keep
  nothing (docs/decisions/saved-boards-live-in-the-browser-or-the-account.md).
