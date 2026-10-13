# Traintrackr moves onto Titan in four steps

Status: accepted (2026-10-13)

## Context

Traintrackr was built before the Titan template existed: a Starlette app on
the MCP SDK, with its own OAuth server and sign-in page, a static site and
its own tooling. Tally and Gondola are on Titan, and get the template's
fixes with `copier update` and their Render setup from `titan`. Traintrackr
gets neither, and every fix to sign-in, security headers or CI has to be
made twice.

Moving it is a port to Django, not a rename. It is too big to review as one
change, and production (traintrackr.live, listed in the Claude and ChatGPT
directories) must keep working at every step.

## Decision

Four stacked PRs, after Gondola's Titan 1/4 to 4/4. Each is mergeable on
its own, and production keeps running `traintracker serve-http` from
`src/traintracker/` until the last one.

1. **On the template.** The template's Django project (`config/`,
   `apps/core/`, `manage.py`), its tooling (tox envs, CI, pre-commit,
   Dependabot, ruff, mypy, coverage) and `.copier-answers.yml`. Python 3.14.
   No page or endpoint changes.
2. **MCP on Titan.** The tools on Titan's `/mcp` view with `mcp-auth` in
   place of `oauth.py`, and Titan's accounts in place of the code sign-in
   (the-train-tools-run-on-one-event-loop.md); the commands become
   management commands. The rail code stays in `src/traintracker/`, which
   production runs, until step 4.
3. **The site in Django.** `/`, `/docs`, `/privacy`, `/terms`, the boards
   and the TV page as templates under DESIGN.md's rules and the strict CSP.
4. **Cut over.** `render.yaml` on Titan's env groups, `src/traintracker/`
   removed. Deploying it is a `titan deploy` by hand. The rail code moved
   into `apps/rail/` and the site's into `apps/public/`; the stdio server
   stays, as `manage.py serve_stdio`. The service answers on its
   onrender.com name again, because `titan deploy` checks `/healthz` there.

What Traintrackr leaves out of the template, for now:

- **The installed app (PWA) and the notes example.** Traintrackr has no
  signed-in pages in a browser; its app is the assistant. `/app/` can come
  later if a board worth installing appears.
- **The domain.** It stays at traintrackr.live, not
  `traintracker.ultraniche.ai`: the directory listings, the MCP Registry
  name and people's connectors all use it.
- **The timetable as Django models.** It stays a raw Postgres schema,
  rebuilt each day and swapped in with a rename, which the ORM has no use
  for. Django's own tables share the database in its default schema.

## Consequences

- Between steps the repository holds two applications: the Django project,
  which nothing deploys yet, and the MCP server, which production runs. The
  server keeps its earlier lint rules and strict mypy until step 2 moves it
  (`pyproject.toml`, per-file ignores).
- Production moves to Python 3.14 with step 1 (`.python-version`).
- `copier update` works from step 1. The parts of the template left out
  above stay out: copier treats them as deleted here.
- Connectors made through the old sign-in are signed out at the cut-over and
  reconnect once, through mcp-auth.
- Titan's accounts keep the email address (the old sign-in kept only a
  hash of it). The privacy page and `privacy_policy` must say so, and they
  change with the step that puts Django in front of people, not before:
  until then they describe what production does.
- The sign-in pages' stylesheet and icons are copies in `static/` of the
  site's files until step 3; a test keeps them identical.
- The passphrase goes: the owner and directory reviewers sign in by email
  like everyone else.
