# Accounts: email sign-in and passkeys

Titan's accounts, as Traintrackr uses them (from Titan 2/4). Nobody signs in
to Traintrackr for its own sake: it happens on the way to connecting Claude
or ChatGPT (`/oauth/authorize/` sends a signed-out person to `/signin/` and
back), and the account page is where a sign-in otherwise ends. The pages are
Traintrackr's own (`templates/accounts/`, `templates/mcp_auth/`), in the
site's stylesheet; the template's versions under `apps/accounts/templates/`
stay as they are so `copier update` merges cleanly. They load no script
from elsewhere, fivebar's included, and don't use `code_input.js`: the code
is one field, as it always was here.

There are no passwords. Someone signs in, and signs up, with an email
address; once in, they can add a passkey from the account page and sign in
with Face ID, a fingerprint or a device PIN from then on.

```
                 ┌──────────── /signin/ ─────────────┐
                 │  email ──▶ SignInRequest (hashes)  │──▶ email: link + 6-digit code
                 │  passkey button / autofill         │
                 └──────┬─────────────────────┬──────┘
          link, any browser                code, this browser only
   /signin/link/<token>/ ── Continue (POST)    /signin/code/ ── POST code
                 └──────────────┬──────────────┘
                     used once ─▶ account found or created ─▶ login() ─▶ next (or /app/account/)

 /app/account/ ── Add a passkey ─▶ options (challenge in session) ─▶ create() ─▶ verify, save Passkey
 /signin/      ── passkey       ─▶ options (challenge in session) ─▶ get()    ─▶ verify, login()
```

## Pieces

| File | Job |
| --- | --- |
| `apps/accounts/sign_in.py` | Issue a request; redeem its link or code once; find or create the account |
| `apps/accounts/passkeys.py` | WebAuthn options and verification (py_webauthn) |
| `apps/accounts/views.py` | The sign-in, code, link, sign-out, account and passkey endpoints |
| `apps/accounts/tasks.py` | `send_sign_in_email`, enqueued by the view (`django.tasks`) |
| `apps/accounts/models.py` | `SignInRequest`, `Passkey` |
| `static/js/passkeys.js`, `webauthn_json.js` | The browser side of both ceremonies |
| `static/js/code_input.js` | The code as six digit boxes; a pasted or autofilled code fills them all |
| `apps/core/ratelimit.py` | Cache-backed limits on emails, codes and passkey challenges |

## Signing in by email

- **One flow for both.** The first redeemed link or code for an address
  creates the account (`username` and `email` both the lowercased address,
  no usable password). Nothing about an address is stored before then
  except the pending request.
- **Link and code, either once.** Each email carries a link and a six-digit
  code, valid for `SIGN_IN_MAX_AGE_SECONDS` (15 minutes). Asking again ends
  the earlier request, so only the newest email works. Only hashes are
  stored: the link token as SHA-256, the code as an HMAC with the secret key.
- **Why a code too.** An app installed on an iPhone home screen keeps its
  own cookies, separate from Safari. A link opened from Mail signs in
  Safari, not the app; the app's user types the code instead.
- **The code only works where it was asked for.** The session holds the
  request's uuid, so a code is useless in another browser, and five wrong
  codes (`SIGN_IN_MAX_CODE_ATTEMPTS`) end it. That is what makes six digits
  enough.
- **Six boxes, one field.** `code_input.js` shows the code as six digit
  boxes: typing moves on, Backspace goes back, and pasting the code (or
  iPhone's one-time-code autofill) into any box fills all six and submits.
  Underneath it is still the one `code` field, which is all a browser
  without JavaScript sees.
- **The link page needs a click.** Mail scanners open links to check them;
  a link that signed in on GET would be used up before the person saw it.
- **No enumeration.** The form answers the same whether or not the address
  has an account. A deactivated account is told so only after it proves it
  owns the address.
- **Limits.** 10 sign-in emails per IP per 10 minutes, 5 per address per
  hour, 20 code tries per IP per 10 minutes, 30 passkey challenges per IP
  per 10 minutes. Behind Render the
  client IP is the last `X-Forwarded-For` hop (`RATE_LIMIT_PROXY_COUNT`).
  The counters live in the cache: point `CACHES` at Redis or the database
  when there is more than one process.
- **`next` survives.** It is checked (`apps/core/redirects.safe_next`),
  stored on the request, and used wherever the link is opened, so signing
  in from the MCP consent page or a deep link comes back there.

## Passkeys

- Added from `/app/account/`, removed there too. Each is named after the
  device it was made on ("Passkey on iPhone").
- Discoverable credentials with user verification required: a passkey alone
  signs someone in, so it has to be the person, not just the device.
- Sign-in needs no email: the browser offers every passkey it holds for the
  site, from the button or from the email field's autofill (conditional
  mediation).
- A passkey deleted here but still on a device answers 404 with its id;
  the page asks the browser to forget it (`signalUnknownCredential`).
- `WEBAUTHN_RP_ID` is the site's host name and `WEBAUTHN_ORIGINS` its
  origin(s), both from `SITE_URL` by default. A passkey only works on the
  host it was made for: moving to a custom domain means users add new ones.

## The reviewer account

The Claude and ChatGPT directories review Traintrackr by connecting it with
a test account, and both refuse a sign-in that needs an inbox, an SMS or any
other verification step. So one address can sign in with a fixed code:

| Setting | Value |
| --- | --- |
| `REVIEWER_EMAIL` | An address with no mailbox behind it, e.g. `reviewer@<our domain>` |
| `REVIEWER_CODE` | Six random digits: `python -c "import secrets; print(f'{secrets.randbelow(10**6):06d}')"` |

Both are secrets on the Render web service (`render.yaml`). With either
unset, or a code that isn't six digits, the account is off and the address
signs in like any other.

- **Nothing else changes.** The reviewer uses the normal pages: email on
  `/signin/`, then the code on `/signin/code/`. That address is sent no
  email; `issue()` gives its request the fixed code instead of a random one.
- **Same limits.** Five wrong codes end a request, and the address gets five
  requests an hour, so the code can't be guessed in any useful time.
- **What it can reach.** An ordinary account: the train tools read public
  rail data, so the worst a leaked code allows is someone else's train
  times. Change `REVIEWER_CODE` after a review, or unset it between reviews.
- **What to give the reviewers.** The address, the code, and: "Enter the
  email on the sign-in page, then type the code on the next page. No email
  is sent."

## Staff and the admin

The admin's password form redirects to `/signin/`. Make the first
superuser with your email as the username, then sign in by email:

```bash
uv run python manage.py createsuperuser --username you@example.com --email you@example.com
```

## Email

Sent through `MAILERS["default"]`: the console in development, SMTP in
production (`EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`,
`EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL`). Views only enqueue
`send_sign_in_email`; `TASKS` uses the immediate backend, which runs it in
the request. Swap in a queue-backed backend and a worker when volume needs it.

## Closing and forgetting accounts

Read-only unless `--commit`:

- `forget_account EMAIL` deletes the account, its passkeys, its connected
  apps' tokens and any sign-in request for the address (a request under
  the privacy policy).
- `block_account EMAIL` closes the account, or makes a closed one for an
  address that has none, so it can't sign in and its connected apps stop
  at once. Reopen it in the admin.
- `purge_idle_accounts` deletes accounts with no sign-in and no new token
  for 180 days (staff never), as the privacy policy promises. A connected
  app's refresh makes a new token, so an account in use is kept.

## Signing out

`POST /signout/` signs out and returns to `/signin/`. Traintrackr has no
installed app, so there is no cache or outbox to clear.
