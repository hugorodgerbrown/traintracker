# Testing

Every check runs through tox, locally and in CI, one CI job per env:

```bash
uv run tox                       # fmt, lint, types, checks, test, audit
uv run tox -e sast               # semgrep (downloads rule packs)
uv run pytest tests/core         # a targeted run while iterating
```

The tests mirror the apps:

| Folder | Covers |
| --- | --- |
| `tests/rail/` | The rail code: importer, STP rules, planner, download, Darwin clients, demo mode, the tools, the commands, rate limit, Darwin usage |
| `tests/public/` | The site's pages and the boards |
| `tests/mcp/` | The `/mcp` endpoint and its tools |
| `tests/accounts/` | Sign-in, passkeys and the account commands |
| `tests/core/` | Titan's shared pieces: models, idempotency, decorators, rate limits, health checks |

The rail tests, and the MCP tests that call the tools, need a Postgres they
can create schemas in, at `TEST_DATABASE_URL` (default
`postgresql://postgres:postgres@127.0.0.1:55432/postgres`); see
"Development" in the README. Each test gets a schema of its own, made and
dropped by `tests/rail/conftest.py`. The Django tests run on SQLite unless
`DATABASE_URL` is set.

## Which layer does a test belong in?

Work top-down and stop at the first layer that can hold the assertion. A
test in the wrong layer is a defect, whatever it asserts.

| Layer | Where | For |
| --- | --- | --- |
| **pytest** | `tests/` | Anything the Django test client, or an MCP client against `/mcp`, can see: status codes, redirects, rendered HTML, JSON, tool results, querysets, commands, headers. **A 401 needs no browser.** |
| **Vitest** | `tests/js/` | Anything a `static/js` file does that jsdom can observe. Not set up yet; arrives with the first test of the site's JavaScript. |
| **Playwright** | `tests/e2e/` | Only what needs a real browser running real JavaScript against a live server. Capped, as in the template; arrives with the first journey that needs one. |

## Rules

- Tests mirror the source tree: `apps/core/views.py` is tested in
  `tests/core/test_views.py`, and `apps/rail/timetable.py` in
  `tests/rail/test_timetable.py`.
- Coverage floor is 90% (`pyproject.toml`); new code comes with tests.
- Factories live in `tests/factories.py`, one per model, always called as
  `Factory.create()` so mypy sees the model type.
- Every datetime has a timezone (ruff's `DTZ` rules enforce it).
- Query counts are asserted on list pages with
  `django_assert_max_num_queries`, so an N+1 fails a test, not production.
- The rail tests use a synthetic SCHEDULE feed
  (`tests/rail/feedgen.py`) and Darwin fixtures shaped on the
  published schema, never live services.
