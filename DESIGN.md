# DESIGN.md — the Titan design rules

Every Titan app has its own design system, kept in its own repository. This
file is the part they share: the rules every one of those design systems
follows. It comes from the
[django-titan-template](https://github.com/hugorodgerbrown/django-titan-template);
change it there, not here, so `copier update` keeps every app on the same
rules. `tests/test_design.py` and `tests/design/` check each rule they can,
and fail the build when one is broken.

## 1. There is a design system, and it is in the code

An app's design system is three things, all in its repository:

- **Tokens**: the custom properties on `:root` in `static/css/app.css`.
  Every colour, font size, space, corner and shadow the app uses is one.
- **Components**: the classes below the tokens in the same file, built only
  from tokens. One stylesheet, no build step.
- **The design page** at `/app/design/` (`apps/design`, staff only): every
  token, read from the stylesheet, and every component in each of its
  states, drawn by the real CSS.

The design page is the reference. A screenshot, a mockup or this file is
not: where they disagree with the page, the page is right.

## 2. Consult it on every UI change

Before changing anything people see:

1. Open the design page and find the component that already does the job.
   Use it as it is.
2. If none does, change or add a component on the design page first, then
   use it in the screen.
3. A pull request that changes the UI shows a screenshot of the screen and,
   if a component changed, of the design page.

A one-off style in a screen (a new colour, a nudge of 3px, a class used
once) is the thing to avoid. Make it a component or a token, or do without.

## 3. Keep it in sync with the code

- The page reads its tokens from `app.css`, so they can't drift.
- Every component class, and every modifier (`--state`), is shown on the
  design page. A component that can't be (the page frame, the launch
  screen) is listed in `apps/design/conf.py` with the reason.
- The page uses only classes that exist in `app.css`.

A change that adds a component, a state or a token adds it to the page in
the same pull request.

## 4. Use tokens; hard-code nothing

Outside `:root`, `app.css` names no raw values for these:

| What | Written as | Not |
| --- | --- | --- |
| Colour | `var(--accent)` | `#2563eb`, `rgba(…)`, `white` |
| Font size | `var(--type-sm)` | `13px`, `0.8125rem` |
| Margin, padding, gap | `var(--space-md)` | `12px`, `0.75rem` |
| Corner | `var(--radius-card)` | `12px`, `50%` |
| Soft shadow | `var(--shadow-float)` | `0 1px 4px rgba(…)` |

Allowed as written: `0`, `auto`, `inherit`, `transparent`, `currentColor`,
`env(…)`, border widths, and a ring drawn as a shadow with no offset or
blur (`inset 0 0 0 2px var(--accent)`). Sizes of things (a 44px button, a
28px spinner) are not spacing and may be literal.

Also:

- **A tint is a token.** A paler or see-through colour (`--accent-soft`) is
  declared once on `:root`, not mixed in a component.
- **Icons take their colour from the text.** An inline SVG uses
  `currentColor`. An SVG in a data URI may only be a `mask-image`, painted
  with a token background.
- **Scripts and templates hold no colours.** A script that needs one reads
  the token with `getComputedStyle`. Emails are the exception: mail clients
  don't read the stylesheet, so they inline their colours.
- **Dark mode redefines tokens, never components.**

## 5. Keep the system small

| Family | Name | Limit |
| --- | --- | --- |
| Colours | any name | 20 |
| Font sizes | `--type-*` | 8 |
| Spacing | `--space-*` | the shared scale below |
| Corners | `--radius-*` | 5 |
| Soft shadows | `--shadow-*` | 4 |

Spacing is one 4px scale for every app. An app declares the steps it uses,
by these names and values, and no others:

| Token | `--space-2xs` | `--space-xs` | `--space-sm` | `--space-md` | `--space-lg` | `--space-xl` | `--space-2xl` | `--space-3xl` | `--space-4xl` |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Value | 2px | 4px | 8px | 12px | 16px | 20px | 24px | 32px | 48px |

Reaching a limit means two tokens are doing one job. Merge them rather than
raising the limit; a limit changes here, for every app, or not at all.

## 6. Prune

- **No dead CSS.** Every class in `app.css` is put on an element by a
  template, a script or Python. A class built from a prefix
  (`chip--{{ kind }}`) counts for every class with that prefix. The
  design page doesn't count: it shows everything. A class a framework adds
  (Django's `errorlist`) is listed in `apps/design/conf.py`.
- **No dead tokens.** Every token is read by the CSS or a script.
- Delete a component's CSS in the same change that removes its last use.

## 7. Accessible by default

- Text tokens keep a contrast of at least 4.5:1 on the backgrounds they
  sit on (3:1 for text 24px and up).
- Anything tapped is at least 44px in its smaller dimension.
- Focus is visible on everything that takes it.
- Colour is never the only signal: a state also changes a shape, an icon
  or a word.

## 8. The website has a footer; the app has its links

Every public page (`public_base.html`) ends with the site footer, the
`{% site_footer %}` tag: links to at least Help, Terms and Privacy, then
`© <this year> <holder>. All rights reserved.` Nothing else: the header
already names the site.

The installed app (`base.html`, under `/app/`) has no footer: it is an
app, not a website. Its account page, behind the header's account link,
lists the same links and copyright with `{% site_links %}`.

The holder and the links are set in `apps/public/conf.py`; the markup and
styles are the shared component. `tests/public/test_footer.py` checks both.

## 9. The top-left mark always goes home

The site's name or icon at the top left links to the front page
(`public:home`) on every page, public or signed in. Nothing else (the
app's first screen, the account page) takes that place; the app's own
navigation does that job.

## 10. Mobile first

A Titan app is used on a phone, in one hand, more than anywhere else.
Design every screen for that first; a wider screen gets more room, never
more features.

- **The base styles are the phone's.** A wider screen is added with
  `@media (min-width: …)`; nothing is taken away with `max-width`.
- **Nothing hides behind hover.** A phone has none, so anything shown on
  hover is also there, or one tap away, without it. Hover only tints.
- **Fit the screen.** Every page has the viewport meta from
  `_head.html`, and an edge fixed to the screen keeps clear of the notch
  and the home bar with `env(safe-area-inset-*)`.
- Tap targets are 44px (section 7), and the main action on a screen sits
  where a thumb reaches it.

## 11. Design for offline

The signal drops in a lift, a tunnel or on a train. The app (everything
under `/app/`) carries on without it.

- **The shell opens offline.** Every script and stylesheet a page under
  `/app/` loads is precached (`apps/pwa/conf.py: PRECACHE_STATIC`). One
  that only works with the network anyway (passkeys) is listed in
  `apps/design/conf.py: NEEDS_NETWORK` with the reason.
- **Writes never fail for want of a signal.** They go through the outbox:
  saved on the device, shown as waiting, sent when the network is back.
- **Say what's offline.** The page frame has the offline banner. Something
  that can't work offline says so where it is, rather than failing
  silently or spinning.
- **Nothing a page needs comes from another origin.** No third-party
  fonts, scripts or stylesheets: they wouldn't be there offline, and the
  CSP blocks them. Analytics are the one exception, and a page works
  without them.

## 12. The front page

Every Titan app's front page (`public/home.html`) has the same anatomy, so
a visitor who has seen one knows where to look on the next. Gondola's is
the model. From top to bottom:

| # | Section | | What it holds |
| --- | --- | --- | --- |
| 1 | Header | required | The icon and name, linking home (§9), and one link on the right: **Sign in**, or **Open the app** when signed in. No avatar and no menu: the account page is inside the app, and the other pages are in the footer. |
| 2 | Proposition | required | An eyebrow if it helps, then the H1, which is the promise, not the app's name (the header already says that). One sentence of lede, then **one** primary action named for the first job ("Start a group", not "Create an account"). Under it, a note that removes the doubt ("Free. Sign in with your email, no password."). |
| 3 | The product, live | required | The app's own component, drawn by the real CSS with demo data, and a one-line caption saying how to read it. |
| 4 | How it works | optional | Three tiles, each an icon, a verb and one line. |
| 5 | From your AI chat | required with MCP | An eyebrow and H2, then a short sample chat (ask, answer, act) and "Try asking" with three or four prompts. Then the `/mcp` address with a copy button, a link to the steps on the help page, and what stays private, said plainly. |
| 6 | Facts | optional | Three true numbers, each removing a doubt ("0 passwords"). |
| 7 | Closing action | required | The promise in other words, and the same primary action again, for whoever scrolled to the end. |
| 8 | Footer | required | §8. Any data credit a source asks for sits above the copyright. |

The rules behind it:

- **One way in.** Signing in creates the account, so there is no separate
  sign-up button. The primary action goes to sign in with `?next=` set to
  the first job's page; "Sign in" lives in the header only.
- **Signed in, the page changes its offer, not its shape.** The header link
  and both primary actions become **Open the app**.
- **Show the product, never a picture of it.** Demo data, not live data: a
  live feed can be empty (no trains at 3am) or wrong, and a screenshot
  goes stale, blurs on a phone and doesn't work in dark mode.
- **The connector steps live on the help page**, in the "Use it from
  Claude or ChatGPT" section (`#use-with-ai`) that every Titan app shares.
  The front page links there and to nothing else for setup; the footer
  links there as "Use with Claude".
- **Every public page unfurls.** `public_base.html` sets the Open Graph and
  Twitter tags, but not `og:url`: the chat already has the link, and a
  sign-in link's address is its token. A page names its own `share_title` and `share_description`
  blocks; the front page uses the promise and the lede. The share card,
  `static/img/share-card.png`, is 1200×630 and shows the proposition and
  the product side by side, so a link pasted in a chat looks like the
  page it opens. `bin/make-icons` draws a placeholder; replace it before
  launch.

## 13. Error pages

Every Titan app answers an error with the same card (`errors/_error_card.html`,
copy in `apps/core/errors.py: ERROR_PAGES`), so a reader who hits one knows
what happened and how to get back:

- **The status, then what happened.** An eyebrow with the code ("Error
  404"), a heading that says it in words ("Page not found"), and one line
  on what to do. No jokes, no illustrations, no stack trace.
- **One way out.** **Go to the front page**, and nothing else to choose
  between. The header's link (Sign in, or Your account) is still there.
- **Traintrackr's own style.** The card is the eyebrow, `h1`, `lede` and
  `button` from `site.css`, in the sign-in pages' narrow column, so an
  error looks like the rest of the site (the Traintrackr design system).
- **403, CSRF and 404 keep the website's frame** (`site_base.html`): the
  site works, the reader only followed a bad link.
- **400 and 500 stand alone** (`errors/standalone.html`): the same header
  and footer, drawn without the request, so the page that reports a
  failure can't fail the same way. Anything added to it must come from the
  app's config, never the session or the database
  (docs/decisions/the-500-page-renders-without-context.md).
- **A client that isn't a browser gets no page**: JSON for the MCP
  endpoint, OAuth and anything asking for JSON; one line of text for htmx.

Traintrackr has no design page, so there is no staff preview; with
`DEBUG` on, Django shows its own pages instead.
