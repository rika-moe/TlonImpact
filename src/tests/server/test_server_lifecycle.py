"""Cancellation owns teardown, including startup and repeated stop requests."""

import asyncio
import signal
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from tlon.base.paths import TlonPaths
from tlon.base.settings import Settings
from tlon_server import serverapp


@pytest.mark.parametrize("windows", [False, True])
@pytest.mark.parametrize("phase", ["initialize", "start"])
async def test_stop_cancels_startup_and_does_not_interrupt_cleanup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, windows: bool, phase: str
) -> None:
    entered = asyncio.Event()
    cleaning = asyncio.Event()
    finish_cleanup = asyncio.Event()
    handlers: list[Callable[..., None]] = []
    restored: list[Any] = []
    previous = signal.SIG_DFL
    loop = asyncio.get_running_loop()

    async def block() -> None:
        entered.set()
        await asyncio.Event().wait()

    async def shutdown() -> None:
        cleaning.set()
        await finish_cleanup.wait()

    app = SimpleNamespace(
        initialize=AsyncMock(), start=AsyncMock(), shutdown=AsyncMock(side_effect=shutdown)
    )
    setattr(app, phase, AsyncMock(side_effect=block))
    monkeypatch.setattr(serverapp, "ServerApp", lambda **kwargs: app)
    monkeypatch.setattr(serverapp, "configure_logging", lambda **kwargs: None)
    monkeypatch.setattr(serverapp, "sys", SimpleNamespace(platform="win32" if windows else "linux"))
    monkeypatch.setattr(signal, "SIGBREAK", signal.SIGTERM, raising=False)
    monkeypatch.setattr(signal, "getsignal", lambda sig: previous)

    def install(sig: int, handler: Any) -> Any:
        if callable(handler):
            handlers.append(handler)
        else:
            restored.append(handler)
        return previous

    monkeypatch.setattr(signal, "signal", install)
    monkeypatch.setattr(loop, "add_signal_handler", lambda sig, handler: handlers.append(handler))
    removed: list[int] = []
    monkeypatch.setattr(loop, "remove_signal_handler", lambda sig: removed.append(sig))
    task = asyncio.create_task(
        serverapp.run_server(tlon_paths=TlonPaths(tmp_path), settings=Settings.from_env())
    )
    await asyncio.wait_for(entered.wait(), 3)
    assert len(handlers) == 1  # Never replaces Runner's SIGINT handler.
    args = (signal.SIGTERM, None) if windows else ()
    handlers[0](*args)
    await asyncio.wait_for(cleaning.wait(), 3)
    handlers[0](*args)
    await asyncio.sleep(0)
    assert not task.done()
    finish_cleanup.set()
    assert await asyncio.wait_for(task, 3) == 0
    app.shutdown.assert_awaited_once()
    assert restored == [previous]
    assert len(removed) == (0 if windows else 1)


@pytest.mark.parametrize("failure", ["error", "cancel"])
async def test_initialize_failure_always_cleans_up(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: str
) -> None:
    error = RuntimeError("startup") if failure == "error" else asyncio.CancelledError()
    app = SimpleNamespace(
        initialize=AsyncMock(side_effect=error), start=AsyncMock(), shutdown=AsyncMock()
    )
    monkeypatch.setattr(serverapp, "ServerApp", lambda **kwargs: app)
    monkeypatch.setattr(serverapp, "configure_logging", lambda **kwargs: None)
    if failure == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await serverapp.run_server(tlon_paths=TlonPaths(tmp_path), settings=Settings.from_env())
    else:
        assert await serverapp.run_server(tlon_paths=TlonPaths(tmp_path), settings=Settings.from_env()) == 1
    app.shutdown.assert_awaited_once()
    app.start.assert_not_awaited()
