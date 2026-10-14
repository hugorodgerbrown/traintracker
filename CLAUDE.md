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

## Tests

pytest for anything the test client or an MCP client can see; Vitest and
Playwright arrive with the site's JavaScript. Coverage floor 90%. Full
rules: [docs/testing.md](docs/testing.md).

## Before opening a PR

Run `uv run tox` and fix every failure. Record a non-obvious architectural
choice in `docs/decisions/`.

## Invariants

1. No `mark_safe()` / `|safe` on content from outside the code.
2. No inline script, style or event handler in a template. Email templates
   are the exception for style: email clients ignore stylesheets, so they
   use inline styles, and they stay very simple.
3. Every fragment view is `@require_htmx`; every outbox endpoint is
   `@login_required_json`.
4. No secrets in source.
5. Email addresses are never stored: accounts and sign-in requests hold
   `email_key(address)` (lower-cased, then keyed-hashed). Never log one.
6. Sign-in secrets (codes, tokens) are stored only as hashes and never
   logged.
