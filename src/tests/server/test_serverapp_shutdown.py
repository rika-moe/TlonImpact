"""Unit tests for ``ServerApp.shutdown`` idempotency.

A kill used to end in a traceback: the signal handler's shutdown task ran
the full teardown, then ``run_server``'s ``finally`` called ``shutdown()``
a second time — the re-entry guard only covered the never-initialized
case, so the second pass re-published ``SYSTEM_SHUTDOWN`` onto the
already-stopped EventBus and raised ``RuntimeError``. Shutdown is now
guarded at entry: sequential and concurrent re-entry both no-op.
"""

import asyncio
from pathlib import Path
from typing import cast

from tlon.api.bootstrap import Runtime
from tlon.base.events.event import Event, EventType
from tlon.base.paths import TlonPaths
from tlon_server.serverapp import ServerApp


class _StubEventBus:
    def __init__(self) -> None:
        self.published: list[EventType] = []

    async def publish(self, event: Event) -> None:
        self.published.append(event.type)


class _StubRuntime:
    def __init__(self) -> None:
        self.event_bus = _StubEventBus()
        self.shutdown_count = 0

    async def shutdown(self) -> None:
        self.shutdown_count += 1


def _started_app(tmp_path: Path) -> tuple[ServerApp, _StubRuntime]:
    """A ServerApp past initialize(): running, with a stub runtime wired in."""
    app = ServerApp(tlon_paths=TlonPaths(tmp_path))
    runtime = _StubRuntime()
    app._runtime = cast(Runtime, runtime)
    app._running = True
    return app, runtime


async def test_shutdown_is_idempotent(tmp_path: Path) -> None:
    """A second shutdown (run_server's finally) does nothing: SYSTEM_SHUTDOWN
    is published exactly once and the runtime is torn down exactly once."""
    app, runtime = _started_app(tmp_path)

    await app.shutdown()
    await app.shutdown()

    assert runtime.event_bus.published == [EventType.SYSTEM_SHUTDOWN]
    assert runtime.shutdown_count == 1


async def test_concurrent_shutdown_tasks_run_teardown_once(tmp_path: Path) -> None:
    """SIGINT+SIGTERM spawn two shutdown tasks; the flag is set before the
    first await, so the second task bails immediately."""
    app, runtime = _started_app(tmp_path)

    await asyncio.gather(app.shutdown(), app.shutdown())

    assert runtime.event_bus.published == [EventType.SYSTEM_SHUTDOWN]
    assert runtime.shutdown_count == 1


async def test_shutdown_before_initialize_is_a_noop(tmp_path: Path) -> None:
    """The finally-path shutdown after a failed initialize() must not raise
    or touch the (uninitialized) runtime/pillars."""
    app = ServerApp(tlon_paths=TlonPaths(tmp_path))

    await app.shutdown()

    assert app._shutdown_started
