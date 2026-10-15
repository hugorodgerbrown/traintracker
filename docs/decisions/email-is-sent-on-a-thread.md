# Email is sent on a thread, not in the request and not from a queue

Status: accepted (2026-10-13)

## Context

Views never send mail; they enqueue a `django.tasks` task. But the only
backend Django ships that runs tasks, `ImmediateBackend`, runs them inside
the request that enqueued them, so a slow connection to the mail relay
(Resend) held the sign-in page for as long as the send took. A queue-backed
backend needs a worker process, a second Render service per app, which a
handful of sign-in emails doesn't justify.

## Decision

- `TASKS` uses `apps.core.task_backends.ThreadBackend`: Django's immediate
  backend, but each task starts on a new thread named `task-<id>` and
  `enqueue` returns at once. The task code, its signals and its result are
  Django's own.
- The thread is not a daemon, so a process shutting down (a deploy) waits
  for a send in progress; SMTP's 10 second timeout bounds the wait.
- A failure is logged by Django's `task_finished` receiver (`django.tasks`,
  ERROR, with the traceback); nothing retries it. Someone whose email never
  came asks for another from the code page.
- Tests run tasks inline (`tests/conftest.py`), so an email is in the
  outbox before the response; `tests/core/test_task_backends.py` puts the
  thread backend back to test it.

## Consequences

- The sign-in page's response time no longer depends on the mail relay.
- A send lost to a crash is gone, as it was mid-request before. When an app
  needs retries or volume, it swaps `TASKS` for a queue-backed backend and
  a worker; no view changes.
- `ThreadBackend` overrides `ImmediateBackend._execute_task`, a private
  method. Django is pinned to a minor release, and the backend's tests fail
  if a release changes it.
