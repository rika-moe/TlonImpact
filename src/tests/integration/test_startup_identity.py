"""Cold starts preserve the address boundary after supported offline edits."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from tlon.agents.prototypes.instantiate import instantiate
from tlon.base.paths import TlonPaths
from tlon.base.settings import Settings
from tlon_server import serverapp
from tlon_server.core.local_identity import LocalIdentity
from tlon_server.serverapp import ServerApp


async def _first_boot(paths: TlonPaths) -> None:
    """Seed a real instance before persisting identity and editing offline."""
    app = ServerApp(tlon_paths=paths, settings=Settings.from_env())
    try:
        await app.initialize()
    finally:
        await app.shutdown()


@pytest.mark.parametrize("agent_id", ["alice", "Alice"])
async def test_restart_rejects_offline_identity_collision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent_id: str
) -> None:
    paths = TlonPaths(tmp_path)
    await _first_boot(paths)
    LocalIdentity(paths.webui_identity_file()).initialize("alice")
    instantiate("developer", agent_id, "Offline member", model="openai/gpt-test", tlon_paths=paths)

    # Observe process-level teardown without replacing the real runtime.
    tracing_shutdown = Mock(wraps=serverapp.shutdown_tracing)
    metrics_shutdown = Mock(wraps=serverapp.shutdown_metrics)
    monkeypatch.setattr("tlon_server.serverapp.shutdown_tracing", tracing_shutdown)
    monkeypatch.setattr("tlon_server.serverapp.shutdown_metrics", metrics_shutdown)
    app = ServerApp(tlon_paths=paths, settings=Settings.from_env())
    try:
        with pytest.raises(ValueError, match="WebUI identity 'alice' conflicts"):
            await app.initialize()
        assert app.rest is None  # Failure occurs before HTTP assembly/listening.
        assert app.client_gateway is None
        assert app._runtime is not None
        assert not app._runtime.event_bus.is_running()
        assert app._shutdown_event.is_set()
        tracing_shutdown.assert_called_once_with()
        metrics_shutdown.assert_called_once_with()
        # run_server's finally may safely call shutdown again.
        await app.shutdown()
        tracing_shutdown.assert_called_once_with()
        metrics_shutdown.assert_called_once_with()
    finally:
        await app.shutdown()


@pytest.mark.parametrize("username", [None, "alice"])
async def test_restart_accepts_distinct_agent_or_uninitialized_identity(
    tmp_path: Path, username: str | None
) -> None:
    paths = TlonPaths(tmp_path)
    await _first_boot(paths)
    if username is not None:
        LocalIdentity(paths.webui_identity_file()).initialize(username)
    instantiate("developer", "bob", "Offline member", model="openai/gpt-test", tlon_paths=paths)
    app = ServerApp(tlon_paths=paths, settings=Settings.from_env())
    try:
        await app.initialize()
        assert app._runtime is not None
        assert "bob" in app._runtime.members.list_ids()
        assert app.rest is not None
        assert app.rest._handler.identity.username == username
    finally:
        await app.shutdown()
