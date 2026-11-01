"""Managed infrastructure must not block mail or bypass the browser boundary."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

from aiohttp.test_utils import TestClient, TestServer

from tlon.api.bootstrap import Runtime
from tlon.api.gitea import GiteaFacade
from tlon.base.paths import TlonPaths
from tlon_server.api.rest import RESTAPIHandler
from tlon_server.core.local_identity import LocalIdentity
from tlon_server.serverapp import ServerApp


async def test_status_requires_browser_boundary_and_health_has_no_details(tmp_path: Path) -> None:
    gitea = GiteaFacade(TlonPaths(tmp_path))
    identity = LocalIdentity(tmp_path / "identity.json")
    handler = RESTAPIHandler(None, identity=identity, gitea=gitea)
    async with TestClient(TestServer(handler.create_app())) as client:
        response = await client.get("/admin/gitea")
        assert response.status == 403
        response = await client.post(
            "/identity", headers={"X-Tlon-WebUI": "1"}, json={"username": "operator"}
        )
        assert response.status == 201
        response = await client.get("/admin/gitea", headers={"X-Tlon-WebUI": "1"})
        assert response.status == 200
        status = await response.json()
        assert status == gitea.status()
        assert status["state"] == "not_started"
        assert not {"password", "token", "admin_password"} & status.keys()
        response = await client.get("/health")
        assert response.status == 200
        health = await response.json()
        assert "gitea" not in health
        assert "credentials_file" not in health


async def test_slow_gitea_does_not_delay_http_and_shutdown_cleans_task(tmp_path: Path) -> None:
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def ensure_started() -> dict[str, object]:
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
        return {}

    runtime = SimpleNamespace(
        gitea=SimpleNamespace(ensure_started=ensure_started),
        members=SimpleNamespace(list_ids=lambda: []),
        event_bus=SimpleNamespace(publish=AsyncMock()),
        shutdown=AsyncMock(),
    )
    app = ServerApp(tlon_paths=TlonPaths(tmp_path))
    app._runtime = cast(Runtime, runtime)
    app.rest = SimpleNamespace(start=AsyncMock(), stop=AsyncMock(), port=8081)
    app.client_gateway = SimpleNamespace(port=0, shutdown=AsyncMock())
    task = asyncio.create_task(app.start())
    try:
        await asyncio.wait_for(app.ready.wait(), 1)
        await asyncio.wait_for(entered.wait(), 1)
        app.rest.start.assert_awaited_once()
        assert not task.done()
        await app.shutdown()
        await asyncio.wait_for(task, 1)
        assert cancelled.is_set()
        runtime.shutdown.assert_awaited_once()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_unavailable_gitea_keeps_core_server_running(tmp_path: Path) -> None:
    status = {
        "state": "unavailable",
        "url": "http://127.0.0.1:3000/",
        "message": "Docker unavailable",
    }
    runtime = SimpleNamespace(
        gitea=SimpleNamespace(ensure_started=AsyncMock(return_value=status)),
        members=SimpleNamespace(list_ids=lambda: []),
        event_bus=SimpleNamespace(publish=AsyncMock()),
        shutdown=AsyncMock(),
    )
    app = ServerApp(tlon_paths=TlonPaths(tmp_path))
    app._runtime = cast(Runtime, runtime)
    app.rest = SimpleNamespace(start=AsyncMock(), stop=AsyncMock(), port=8081)
    app.client_gateway = SimpleNamespace(port=0, shutdown=AsyncMock())
    task = asyncio.create_task(app.start())
    try:
        await asyncio.wait_for(app.ready.wait(), 1)
        assert app._gitea_start_task is not None
        await app._gitea_start_task
        assert app._running
        assert not task.done()
        app.rest.stop.assert_not_awaited()
        await app.shutdown()
        await asyncio.wait_for(task, 1)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
