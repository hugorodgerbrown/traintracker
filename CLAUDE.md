# CLAUDE.md — Traintrackr

Live GB train times, platforms and journeys, in Claude and ChatGPT. It is
built on the Titan
[django-titan-template](https://github.com/hugorodgerbrown/django-titan-template)
([docs/decisions/traintrackr-moves-onto-titan-in-four-steps.md](docs/decisions/traintrackr-moves-onto-titan-in-four-steps.md));
keep new work close to the template so `copier update --trust` stays a clean
merge. Deploying is `titan deploy` (`render.yaml`).

## Layout

```
config/            settings (one module, env-driven), urls, wsgi/asgi
apps/core/         BaseModel, IdempotencyMiddleware + IdempotencyRecord,
                   decorators (require_htmx, login_required_json), rate
                   limits, livez and healthz
apps/accounts/     email link or code sign-in, passkeys, the account
                   commands (docs/accounts.md)
apps/mcp/          /mcp (Titan's view, mcp-auth) listing the train tools
apps/rail/         the train tools (server.py), their event loop
                   (runtime.py), the timetable, planner and Darwin client,
                   the board MCP App (ui/), and the rail commands
apps/public/       the public site: pages, departure boards, embed, TV view
templates/         Traintrackr's sign-in, account and connect pages
static/            site.css, site.js, icons and the share card
docs/              testing, decisions/
.claude/skills/    writing the Titan docs kept in Linear (research, user
                   testing script, blog post), each with a length setting
```

## Commands

```bash
uv sync
uv run python manage.py runserver
uv run python manage.py serve_stdio   # the tools on stdio (desktop app, MCP Inspector)
uv run tox                            # every check CI runs (except sast)
uv run pytest tests/core              # targeted run while iterating
```

Never run a bare `pytest`; use `uv run`. Dependencies: `uv add` /
`uv add --group <test|lint|types> …`, then commit `uv.lock`.

## Code conventions

- A module docstring on every module and a docstring on every class and
  function (ruff `D`, Google style); every argument typed.
- British English in prose and identifiers (colour, behaviour), except
  third-party names.
- Simple over clever; no abstraction until two callers need it.
  Composition over inheritance.
- `logging.getLogger(__name__)` in every module; no `print` outside the
  command line.
- No Django signals for side effects: call them from the service function.
- Secrets only through environment variables; never in source.

### Models

Every concrete model ships the full kit: inherits `BaseModel`; has an
admin class, a `to_string()`, an explicit `Meta.ordering`, a custom
queryset, a factory in `tests/factories.py`, and tests. Use `uuid`, never
`id`, in URLs and anything a client sees. The timetable is not a model: it
is a Postgres schema rebuilt each day (`apps/rail/timetable.py`).

### Management commands

Run with no arguments to do the common thing, read-only unless `--commit`,
respect `--verbosity`, exit non-zero on failure.

## Design system

[DESIGN.md](DESIGN.md) holds the rules every Titan app shares, copied from
the template unchanged. The front page (`apps/public/templates/public/pages/index.html`)
follows its section 12, "The front page". Traintrackr has no `/app/` and no
design page (`apps/design`), so where DESIGN.md names `app.css` or the design
page, read `static/css/site.css` and the artifact below.

The project's design system is the "Traintrackr" Design System artifact:
https://claude.ai/artifact/BXsWTZk9w2ESAwKwKDPgab

Before changing anything a person sees, read the system's `project/README.md` with the Artifact tool (`action: "read"`,
`path: "project/README.md"`), and `project/tokens.json` or
`project/components/<Name>/README.md` where the change touches them. Follow its
colour, type, spacing, board and copy rules. That covers:

- `apps/public/templates/` and `static/`: the pages, boards, `site.css`,
  `site.js`, icons and share card.
- `apps/rail/ui/board.html`: the board drawn in Claude and ChatGPT.
- `templates/accounts/` and `templates/mcp_auth/`: the sign-in, account and
  connect pages, which use the site stylesheet and icon.
- `templates/accounts/emails/`: the sign-in email's subject and body (copy rules).

The system was built from this repository's code, so the code is the source and
the artifact documents it. When a change alters a token, a component or a rule
the README states, say so in the PR and offer to update the artifact to match.

## Linear

Traintrackr predates the Titan team's one-project-per-app layout: its
issues live in its own Linear team, Traintrackr (issue keys `TRA-`), and
every change maps to an issue there: one issue per PR, one PR per issue. Why:
[docs/decisions/every-change-starts-as-a-linear-issue.md](docs/decisions/every-change-starts-as-a-linear-issue.md).

- If the project doesn't exist yet (a new app), create it in the Titan
  team first, named after the app, then file the first issue in it.
- The issue is the sign-off. Before writing code, Claude creates it in
  the app's project with the scope: what changes, what doesn't, how
  we'll know it's done, and the decision behind it. Work starts once the
  person agrees the scope, not before.
- If the scope changes during the work, say so on the issue before the
  PR goes up, so the issue records what was built and why.
- The PR title is `TRA-42: what it does` and its body starts
  `Closes TRA-42`, so Linear links the two and closes the issue on merge.
- Reversing an earlier decision is a new issue that links the old one
  and says why it changed. Never rewrite the old issue: the history is
  the point.
- Dependabot's PRs are the one exception: they need no issue.
- Without the Linear MCP server, say so and give the issue as Markdown
  for the person to file; don't start unticketed work.

## Branches

Every new branch starts by pulling in the template, so the app never
drifts far from it and the template's changes are reviewed apart from the
branch's own work.

- Before any other change, on a clean tree, run `copier update --trust`.
- Resolve any conflicts it leaves, keeping the app's deliberate
  differences (their reasons are in `docs/decisions/`), then run
  `uv run tox`.
- Commit the result on its own as the branch's first commit, for example
  `TRA-42: Update from the Titan template`, before starting the issue's
  work.
- If the update changes nothing, there is nothing to commit: skip it,
  never make an empty commit.

## Tests

pytest for anything the test client or an MCP client can see; Vitest and
Playwright arrive with the site's JavaScript. Coverage floor 90%. Full
rules: [docs/testing.md](docs/testing.md).

## Before opening a PR

Run `uv run tox` and fix every failure. Check the PR names its Linear
issue and its first commit is the template update, if there was one
(above). Record a non-obvious architectural choice in
`docs/decisions/`.

Keep the docs in sync with the code in the same PR, never in a follow-up. A
change to what is stored, logged or sent anywhere updates every page that
describes it: `privacy.html` and `terms.html` in `apps/public/templates/public/pages/`,
the account and sign-in pages, `docs.html`, `index.html`, the README and
`docs/accounts.md`. Each page says what the code on its own branch does.

## Invariants

1. No `mark_safe()` / `|safe` on content from outside the code.
2. No inline script, style or event handler in a template. Email templates
   are the exception for style: email clients ignore stylesheets, so they
   use inline styles, and they stay very simple.
3. Every fragment view is `@require_htmx`; every outbox endpoint is
   `@login_required_json`.
4. No secrets in source.
5. Email addresses are lower-cased before storage and lookup.
6. Sign-in secrets (codes, tokens) are stored only as hashes and never
   logged. The one exception is a reviewer account's fixed code, which
   staff read in the admin (docs/accounts.md).
7. Who may connect an MCP client (`MCP_AUTH["CAN_CONNECT"]`) only widens in
   a PR that says so.
