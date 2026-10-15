"""A django.tasks backend that runs each task on a thread of its own.

Django's ImmediateBackend runs a task inside the request that enqueues it,
so a slow mail relay holds the sign-in page for as long as the send takes.
ThreadBackend runs the same code on a new thread and returns at once: the
response goes out while the email is still being sent.

At most ``MAX_THREADS`` tasks run on threads at once. One enqueued past
that runs in the request instead, as ImmediateBackend would, so a burst of
sign-ins slows down rather than opening threads and mail connections without
end. There is no queue and no worker. A task still running when its process
stops waits for it (the thread is not a daemon), but one lost to a crash is
gone, as it would be half way through a request. A task that fails is
logged by Django's own ``task_finished`` receiver: ``django.tasks``, at
ERROR, with the traceback. Tests swap in ImmediateBackend
(``tests/conftest.py``) so mail lands in the outbox before the response.
"""

from __future__ import annotations

import threading

from django.db import connections
from django.tasks import TaskResult
from django.tasks.backends.immediate import ImmediateBackend

MAX_THREADS = 8

# Shared by every backend instance in the process: one slot per running thread.
_slots = threading.BoundedSemaphore(MAX_THREADS)


class ThreadBackend(ImmediateBackend):
    """Run each enqueued task on a new thread instead of in the request."""

    def _execute_task(self, task_result: TaskResult) -> None:
        """Start the task on a thread named after it, and return; run it here if none is free."""
        if not _slots.acquire(blocking=False):
            # django-stubs leaves out this private method; Django has it (see the tests).
            super()._execute_task(task_result)  # type: ignore[misc]
            return
        thread = threading.Thread(
            target=self._run,
            args=(task_result,),
            name=f"task-{task_result.id}",
        )
        try:
            thread.start()
        except RuntimeError:
            # The system refused a new thread: give the slot back and run it here.
            _slots.release()
            super()._execute_task(task_result)  # type: ignore[misc]

    def _run(self, task_result: TaskResult) -> None:
        """Run the task as ImmediateBackend would, then close this thread's connections."""
        try:
            # django-stubs leaves out this private method; Django has it (see the tests).
            super()._execute_task(task_result)  # type: ignore[misc]
        finally:
            # Django opens database connections per thread; this one ends here.
            connections.close_all()
            _slots.release()
