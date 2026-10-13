"""The train tools' event loop: one per process, in a thread of its own.

The tools are coroutines on ``traintracker.server``'s MCP server. They share
one HTTP client, the day's Darwin request budget, and a semaphore that holds
timetable reads and journey plans to two at a time; the server's lifespan
makes them and starts the daily timetable refresh. Titan's ``/mcp`` view is
synchronous, and a new event loop per request would make all of that per
request too.

So the process keeps one loop running in a daemon thread. The first call
starts it and enters the server's lifespan there; every request thread then
hands its coroutine to that loop and waits for the answer. Production runs
one gunicorn worker with threads (the journey planner's network is held in
memory once a process): see
docs/decisions/the-train-tools-run-on-one-event-loop.md.
"""

import asyncio
import logging
import threading
from collections.abc import Callable, Coroutine
from contextlib import AsyncExitStack
from typing import Any

from traintracker import server

logger = logging.getLogger(__name__)

# The lifespan builds the demo timetable on a first run, which takes a while.
START_TIMEOUT = 300.0
# Longer than any one tool takes: a journey plan waits up to 10 seconds for
# its turn (server.BUSY_AFTER), then a few seconds of work and Darwin calls.
CALL_TIMEOUT = 60.0

_lock = threading.Lock()
_loop: asyncio.AbstractEventLoop | None = None
_thread: threading.Thread | None = None
_stack: AsyncExitStack | None = None


def _start() -> asyncio.AbstractEventLoop:
    """Start the loop's thread and enter the server's lifespan on it."""
    global _loop, _thread, _stack
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, name="rail-loop", daemon=True)
    thread.start()
    stack = AsyncExitStack()
    try:
        asyncio.run_coroutine_threadsafe(
            stack.enter_async_context(server.lifespan(server.mcp)), loop
        ).result(START_TIMEOUT)
    except BaseException:
        loop.call_soon_threadsafe(loop.stop)
        thread.join()
        loop.close()
        raise
    logger.info("Train tools started")
    _loop, _thread, _stack = loop, thread, stack
    return loop


def loop() -> asyncio.AbstractEventLoop:
    """Return the process's loop, starting it on the first call."""
    with _lock:
        return _loop if _loop is not None else _start()


def run[T](
    fn: Callable[..., Coroutine[Any, Any, T]], *args: Any, timeout: float = CALL_TIMEOUT
) -> T:
    """Run ``fn(*args)`` on the loop and return its result, from any thread.

    ``fn`` is called on the loop, not here, so nothing is left un-awaited if
    the loop can't start. A call that takes longer than ``timeout`` is
    cancelled and raises ``TimeoutError``.
    """

    async def call() -> T:
        return await fn(*args)

    future = asyncio.run_coroutine_threadsafe(call(), loop())
    try:
        return future.result(timeout)
    except TimeoutError:
        future.cancel()
        raise


def stop() -> None:
    """Leave the lifespan and stop the loop (tests, and a worker shutting down)."""
    global _loop, _thread, _stack
    with _lock:
        if _loop is None or _stack is None or _thread is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(_stack.aclose(), _loop).result(CALL_TIMEOUT)
        finally:
            _loop.call_soon_threadsafe(_loop.stop)
            _thread.join()
            _loop.close()
            _loop, _thread, _stack = None, None, None
