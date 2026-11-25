# The sign-in email is a one-time code email, with the link as a shortcut

Status: accepted (2026-11-25)

## Context

Gmail (Android and iOS from September 2026, and the web) shows a **Copy
code** card or pill on emails it reads as one-time verification codes.
Google publishes no rule for senders. Six emails that Gmail does recognise
(Booking.com, Screwfix, Onelink, DHL, Neko Health, Fresha) share one thing:
the email is about a one-time code, named in plain words ("one-time",
"verification code", "passcode") beside the number. Layout and subject vary:
DHL's code sits in a sentence and its subject has no code. Ours led with a
long link and called it "Sign in to …", then offered the code second, in
plain text only.

## Decision

- The email is a code email: "Your one-time verification code", the code,
  the expiry, the link, the ignore line. Nothing else (DESIGN.md, "The
  sign-in email").
- The subject is "Your one-time {app} verification code: {code}".
- It is sent as plain text and HTML (`EmailMultiAlternatives`), saying the
  same thing.
- The link keeps its own long token. It reads as a shortcut ("Sign in with
  this code"), but the six digits never go in a URL, where logs and browser
  history would keep a guessable secret.
- An `OTP-Token` header (draft-goto-otp-token-01) names the code and the
  origin. It is a draft; Gmail is not known to read it.

## Consequences

- Whether Gmail shows the card is its choice, not ours: it may change, and
  test it from the real sending domain, not localhost.
- `tests/accounts/test_views.py` holds the order (code before link) and the
  wording, so a later edit can't drift back to a link email.
