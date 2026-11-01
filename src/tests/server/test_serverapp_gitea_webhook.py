"""Notification listener failure never takes down the mail runtime."""

import asyncio
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tlon.api.bootstrap import Runtime
from tlon.base.paths import TlonPaths
from tlon_server.serverapp import ServerApp


def _app(tmp_path: Path) -> tuple[ServerApp, MagicMock]:
    app = ServerApp(tlon_paths=TlonPaths(tmp_path))
    runtime = MagicMock()
    runtime.gitea.ensure_started = AsyncMock(
        return_value={"state": "ready", "url": "http://localhost", "message": "ready"}
    )
    runtime.gitea.webhooks.start = AsyncMock()
    runtime.event_bus.publish = AsyncMock()
    runtime.shutdown = AsyncMock()
    app._runtime = cast(Runtime, runtime)
    return app, runtime


async def test_listener_starts_only_after_gitea_ready(tmp_path: Path) -> None:
    app, runtime = _app(tmp_path)
    with patch("tlon_server.serverapp.GiteaWebhookServer") as listener_type:
        listener = listener_type.return_value
        listener.start = AsyncMock()
        listener.port = 43210
        await app._start_gitea()
        listener_type.assert_called_once_with(
            runtime.gitea.webhooks, app._settings.gitea_webhook_port
        )
        listener.start.assert_awaited_once()
        runtime.gitea.webhooks.start.assert_awaited_once_with(43210)


async def test_unavailable_gitea_does_not_bind_webhook(tmp_path: Path) -> None:
    app, runtime = _app(tmp_path)
    runtime.gitea.ensure_started.return_value["state"] = "unavailable"
    with patch("tlon_server.serverapp.GiteaWebhookServer") as listener_type:
        await app._start_gitea()
        listener_type.assert_not_called()


async def test_webhook_failure_is_safe_and_does_not_stop_runtime(tmp_path: Path) -> None:
    app, runtime = _app(tmp_path)
    with patch("tlon_server.serverapp.GiteaWebhookServer") as listener_type:
        listener = listener_type.return_value
        listener.start = AsyncMock(side_effect=OSError("secret error details"))
        listener.stop = AsyncMock()
        await app._start_gitea()
        listener.stop.assert_awaited_once()
        runtime.gitea.webhooks.unavailable.assert_called_once_with(
            "Gitea notification listener could not start"
        )
        runtime.shutdown.assert_not_awaited()


async def test_start_cancellation_stops_listener(tmp_path: Path) -> None:
    app, runtime = _app(tmp_path)
    runtime.gitea.webhooks.start.side_effect = asyncio.CancelledError
    with patch("tlon_server.serverapp.GiteaWebhookServer") as listener_type:
        listener = listener_type.return_value
        listener.start = AsyncMock()
        listener.stop = AsyncMock()
        with pytest.raises(asyncio.CancelledError):
            await app._start_gitea()
        listener.stop.assert_awaited_once()


async def test_shutdown_stops_webhook_before_runtime(tmp_path: Path) -> None:
    app, runtime = _app(tmp_path)
    order: list[str] = []

    async def stop_listener() -> None:
        order.append("listener")

    async def stop_runtime() -> None:
        order.append("runtime")

    listener = MagicMock()
    listener.stop = AsyncMock(side_effect=stop_listener)
    app._gitea_webhook = listener
    runtime.shutdown.side_effect = stop_runtime
    await app.shutdown()
    await app.shutdown()
    assert order == ["listener", "runtime"]
