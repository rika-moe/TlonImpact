"""Tests for the tlon CLI subcommands (tlon_launcher.commands)."""

import argparse
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from tlon_launcher.commands.dream import run_dream


def _dream_args(**overrides: Any) -> argparse.Namespace:
    base: dict[str, Any] = {"host": "127.0.0.1", "rest_port": 8080, "debug": False}
    return argparse.Namespace(**(base | overrides))


class TestRunDream:
    def _patch_run_server(self, monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
        # run_dream imports run_server lazily inside the function body,
        # so the patch target is the source module, not the launcher module.
        run_server = AsyncMock(return_value=0)
        monkeypatch.setattr("tlon_server.serverapp.run_server", run_server)
        return run_server

    def _infra(self, log_level: str = "INFO") -> tuple[Any, Any]:
        """The entry-point injectables: a stand-in TlonPaths and a settings
        snapshot with the given log level."""
        return SimpleNamespace(name="fake-paths"), SimpleNamespace(log_level=log_level)

    async def test_passthrough_and_exit_code(self, monkeypatch: pytest.MonkeyPatch) -> None:
        run_server = self._patch_run_server(monkeypatch)
        run_server.return_value = 7
        paths, settings = self._infra(log_level="WARNING")

        result = await run_dream(_dream_args(host="0.0.0.0", rest_port=9090), paths, settings)

        assert result == 7
        run_server.assert_awaited_once_with(
            host="0.0.0.0",
            rest_port=9090,
            log_level="WARNING",
            debug=False,
            tlon_paths=paths,
            settings=settings,
        )

    async def test_lucid_forces_debug_log_level(self, monkeypatch: pytest.MonkeyPatch) -> None:
        run_server = self._patch_run_server(monkeypatch)
        paths, settings = self._infra()

        result = await run_dream(_dream_args(debug=True), paths, settings)

        assert result == 0
        run_server.assert_awaited_once_with(
            host="127.0.0.1",
            rest_port=8080,
            log_level="DEBUG",
            debug=True,
            tlon_paths=paths,
            settings=settings,
        )
