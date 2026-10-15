"""Tests for apps.core.task_backends: tasks run on a thread of their own."""

import logging
import threading
from typing import Any

import pytest
from django.core import mail
from django.core.cache import cache
from django.core.mail import send_mail
from django.tasks import task
from django.tasks.base import TaskResultStatus
from django.test import Client
from django.urls import reverse

THREADED = {"default": {"BACKEND": "apps.core.task_backends.ThreadBackend"}}

# Module-level, because a task's arguments must be JSON: the test holds the
# task on ``release`` and reads what it saw from ``seen``.
release = threading.Event()
seen: dict[str, Any] = {}


@task()
def note_thread() -> None:
    """Record the thread this task ran on, once released."""
    release.wait(timeout=5)
    seen["thread"] = threading.current_thread()


@task()
def note_caller() -> None:
    """Record the thread this task ran on, at once."""
    seen["caller"] = threading.current_thread()


@task()
def fail() -> None:
    """Fail, as a send to an unreachable mail relay would."""
    raise ConnectionRefusedError("mail relay unreachable")


started: list[threading.Thread] = []


class RecordedThread(threading.Thread):
    """A thread that notes itself as it starts, so a test can join it after it ends."""

    def start(self) -> None:
        """Record this thread, then start it."""
        started.append(self)
        super().start()


@pytest.fixture(autouse=True)
def _threaded(settings: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Use the app's own backend, which tests otherwise swap out, and record its threads."""
    settings.TASKS = THREADED
    monkeypatch.setattr("apps.core.task_backends.threading.Thread", RecordedThread)
    release.clear()
    seen.clear()
    started.clear()


def join(name: str) -> threading.Thread:
    """Wait for the task thread called ``name`` to finish, and return it.

    Found among the threads recorded as they started, not the live ones: a
    task that has already finished is no longer live.
    """
    [thread] = [t for t in started if t.name == name]
    thread.join(timeout=5)
    assert not thread.is_alive()
    return thread


def test_enqueue_returns_before_the_task_runs() -> None:
    """The caller is not held while the task waits; it runs on another thread."""
    result = note_thread.enqueue()
    assert "thread" not in seen
    release.set()
    join(f"task-{result.id}")
    assert seen["thread"] is not threading.current_thread()
    assert result.status == TaskResultStatus.SUCCESSFUL


def test_a_failed_task_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    """A task that raises is logged at ERROR with its traceback, not lost."""
    with caplog.at_level(logging.ERROR, logger="django.tasks"):
        result = fail.enqueue()
        join(f"task-{result.id}")
    assert result.status == TaskResultStatus.FAILED
    [record] = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert record.exc_info
    assert "mail relay unreachable" in caplog.text


def test_a_task_past_the_limit_runs_in_the_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    """With every thread slot taken, the task runs before enqueue returns, on no new thread."""
    monkeypatch.setattr("apps.core.task_backends._slots", threading.BoundedSemaphore(1))
    held = note_thread.enqueue()
    result = note_caller.enqueue()
    assert result.status == TaskResultStatus.SUCCESSFUL
    assert seen["caller"] is threading.current_thread()
    assert [t.name for t in started] == [f"task-{held.id}"]
    release.set()
    join(f"task-{held.id}")


class RefusedThread(threading.Thread):
    """A thread the system refuses to start, as when it is out of threads."""

    def start(self) -> None:
        """Refuse, as ``threading.Thread.start`` does when no thread can be made."""
        raise RuntimeError("can't start new thread")


def test_a_refused_thread_runs_the_task_here_and_frees_its_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A thread that can't start costs no slot: the task runs in the caller instead."""
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr("apps.core.task_backends._slots", slots)
    monkeypatch.setattr("apps.core.task_backends.threading.Thread", RefusedThread)
    result = note_caller.enqueue()
    assert result.status == TaskResultStatus.SUCCESSFUL
    assert seen["caller"] is threading.current_thread()
    assert slots.acquire(blocking=False)


@pytest.mark.django_db
def test_the_sign_in_email_is_sent_off_the_request(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sign-in page answers while its email is still sending, and the email still goes."""
    cache.clear()
    send = threading.Event()

    def slow_send_mail(*args: Any, **kwargs: Any) -> int:
        """Hold the send, as a slow mail relay would, then send it."""
        send.wait(timeout=5)
        seen["thread"] = threading.current_thread()
        return send_mail(*args, **kwargs)

    monkeypatch.setattr("apps.accounts.tasks.send_mail", slow_send_mail)
    response = client.post(reverse("accounts:sign_in"), {"email": "walker@example.com"})
    assert response.status_code == 302
    assert not mail.outbox
    send.set()
    [name] = [t.name for t in started if t.name.startswith("task-")]
    thread = join(name)
    assert seen["thread"] is thread
    assert mail.outbox[0].to == ["walker@example.com"]
