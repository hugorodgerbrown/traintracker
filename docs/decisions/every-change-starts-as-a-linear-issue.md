# Every change starts as a Linear issue, one per PR

Status: accepted (2026-10-13)

## Context

A Titan app is built fast, mostly by Claude, in many short sessions.
The code and the PRs say what changed, but not what was agreed before
the work began, and a session that starts cold has no way to ask. When
a decision is reversed weeks later, nobody can say what the original
one was for, so the reversal can't be judged or the old mistake is made
again. Snowdesk works ticket-first in Linear and keeps that history; the
Titan apps started without it.

## Decision

- Every app has a Linear project in the Titan team, named after the app.
  `titan new` doesn't make it: Claude creates it before the first issue.
- Every PR maps to exactly one issue in that project, and every issue to
  one PR.
- Claude creates the issue before development starts, with the scope.
  The person agreeing that scope is the sign-off; no code is written
  before it.
- The PR title carries the issue key and the body starts
  `Closes RAS-NN`, so Linear's GitHub integration links them and closes
  the issue on merge.
- A reversal is a new issue linked to the one it reverses, saying why.
  Old issues are not rewritten.

## Consequences

- Every session needs the Linear MCP server. Without it, Claude drafts
  the issue as Markdown and waits rather than starting unticketed work.
- Dependabot's version bumps are exempt: they carry no decision, and
  the PR itself is their record.
- Small fixes cost an issue too. That is the price of a complete
  history; split work rather than bundling two issues into one PR.
- The project's Linear issues, not the git log, are where to look first
  for why something is the way it is.
