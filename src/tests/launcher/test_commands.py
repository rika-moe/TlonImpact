"""Tests for the tlon CLI subcommands (tlon_launcher.commands)."""

import argparse
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from tlon_launcher.commands import dream as dream_module
from tlon_launcher.commands import ops as ops_module
from tlon_launcher.commands import telegram as telegram_module
from tlon_launcher.commands.dream import run_dream
from tlon_launcher.commands.ops import _find_operator_command, run_ops
from tlon_launcher.commands.telegram import run_telegram


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

    async def test_passthrough_and_exit_code(self, monkeypatch: pytest.MonkeyPatch) -> None:
        run_server = self._patch_run_server(monkeypatch)
        run_server.return_value = 7
        monkeypatch.setattr(
            dream_module,
            "get_settings",
            lambda: SimpleNamespace(log_level="WARNING"),
        )

        result = await run_dream(_dream_args(host="0.0.0.0", rest_port=9090))

        assert result == 7
        run_server.assert_awaited_once_with(
            host="0.0.0.0",
            rest_port=9090,
            log_level="WARNING",
            debug=False,
        )

    async def test_lucid_forces_debug_log_level(self, monkeypatch: pytest.MonkeyPatch) -> None:
        run_server = self._patch_run_server(monkeypatch)

        result = await run_dream(_dream_args(debug=True))

        assert result == 0
        run_server.assert_awaited_once_with(
            host="127.0.0.1",
            rest_port=8080,
            log_level="DEBUG",
            debug=True,
        )


def _ops_args(**overrides: Any) -> argparse.Namespace:
    base: dict[str, Any] = {
        "host": "127.0.0.1",
        "rest_port": 8080,
        "username": None,
        "debug": False,
    }
    return argparse.Namespace(**(base | overrides))


class TestFindOperatorCommand:
    def test_prefers_built_dist(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        dist_cli = tmp_path / "dist" / "cli.js"
        dist_cli.parent.mkdir(parents=True)
        dist_cli.touch()
        monkeypatch.setattr(ops_module, "OPERATOR_DIR", tmp_path)

        assert _find_operator_command() == ["node", str(dist_cli)]

    def test_falls_back_to_tsx_dev_mode(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        tsx_bin = tmp_path / "node_modules" / ".bin" / "tsx"
        tsx_bin.parent.mkdir(parents=True)
        tsx_bin.touch()
        src_cli = tmp_path / "src" / "cli.tsx"
        src_cli.parent.mkdir(parents=True)
        src_cli.touch()
        monkeypatch.setattr(ops_module, "OPERATOR_DIR", tmp_path)

        assert _find_operator_command() == [str(tsx_bin), str(src_cli)]

    def test_dist_wins_over_tsx(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        dist_cli = tmp_path / "dist" / "cli.js"
        dist_cli.parent.mkdir(parents=True)
        dist_cli.touch()
        tsx_bin = tmp_path / "node_modules" / ".bin" / "tsx"
        tsx_bin.parent.mkdir(parents=True)
        tsx_bin.touch()
        src_cli = tmp_path / "src" / "cli.tsx"
        src_cli.parent.mkdir(parents=True)
        src_cli.touch()
        monkeypatch.setattr(ops_module, "OPERATOR_DIR", tmp_path)

        assert _find_operator_command() == ["node", str(dist_cli)]

    def test_tsx_without_source_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        tsx_bin = tmp_path / "node_modules" / ".bin" / "tsx"
        tsx_bin.parent.mkdir(parents=True)
        tsx_bin.touch()
        monkeypatch.setattr(ops_module, "OPERATOR_DIR", tmp_path)

        assert _find_operator_command() is None

    def test_nothing_installed_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(ops_module, "OPERATOR_DIR", tmp_path)

        assert _find_operator_command() is None


class TestRunOps:
    def _patch_infra(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(ops_module, "configure_logging", lambda **kwargs: None)
        monkeypatch.setattr(ops_module, "OPERATOR_DIR", tmp_path)
        monkeypatch.setattr(ops_module.shutil, "which", lambda name: "/usr/bin/node")

    async def test_node_missing_returns_one(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        self._patch_infra(monkeypatch, tmp_path)
        monkeypatch.setattr(ops_module.shutil, "which", lambda name: None)

        assert await run_ops(_ops_args()) == 1

    async def test_operator_not_installed_returns_one(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        self._patch_infra(monkeypatch, tmp_path)

        assert await run_ops(_ops_args()) == 1

    async def test_lucid_configures_debug_logging(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        log_calls: list[dict[str, Any]] = []
        monkeypatch.setattr(
            ops_module, "configure_logging", lambda **kwargs: log_calls.append(kwargs)
        )
        monkeypatch.setattr(ops_module, "OPERATOR_DIR", tmp_path)
        monkeypatch.setattr(ops_module.shutil, "which", lambda name: None)

        assert await run_ops(_ops_args(debug=True)) == 1
        assert log_calls == [{"log_level": "DEBUG", "debug": True}]

    async def test_launches_subprocess_with_connection_params(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        self._patch_infra(monkeypatch, tmp_path)
        monkeypatch.setattr(ops_module, "_find_operator_command", lambda: ["node", "dist/cli.js"])
        run_calls: list[dict[str, Any]] = []

        def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            run_calls.append({"command": list(command), **kwargs})
            return subprocess.CompletedProcess(command, 0)

        monkeypatch.setattr(ops_module.subprocess, "run", fake_run)

        result = await run_ops(_ops_args(host="10.0.0.1", rest_port=9999, username="alice"))

        assert result == 0
        assert run_calls == [
            {
                "command": [
                    "node",
                    "dist/cli.js",
                    "--host",
                    "10.0.0.1",
                    "--rest-port",
                    "9999",
                    "--username",
                    "alice",
                ],
                "cwd": tmp_path,
                "check": False,
            }
        ]

    async def test_username_omitted_when_not_set(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        self._patch_infra(monkeypatch, tmp_path)
        monkeypatch.setattr(ops_module, "_find_operator_command", lambda: ["node", "dist/cli.js"])
        run_calls: list[list[str]] = []

        def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            run_calls.append(list(command))
            return subprocess.CompletedProcess(command, 5)

        monkeypatch.setattr(ops_module.subprocess, "run", fake_run)

        result = await run_ops(_ops_args())

        assert result == 5
        assert "--username" not in run_calls[0]

    async def test_keyboard_interrupt_returns_130(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        self._patch_infra(monkeypatch, tmp_path)
        monkeypatch.setattr(ops_module, "_find_operator_command", lambda: ["node", "dist/cli.js"])

        def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            raise KeyboardInterrupt

        monkeypatch.setattr(ops_module.subprocess, "run", fake_run)

        assert await run_ops(_ops_args()) == 130

    async def test_unexpected_error_returns_one(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        self._patch_infra(monkeypatch, tmp_path)
        monkeypatch.setattr(ops_module, "_find_operator_command", lambda: ["node", "dist/cli.js"])

        def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            raise OSError("boom")

        monkeypatch.setattr(ops_module.subprocess, "run", fake_run)

        assert await run_ops(_ops_args()) == 1


class TestRunTelegram:
    def _args(self, **overrides: Any) -> argparse.Namespace:
        base: dict[str, Any] = {
            "host": "127.0.0.1",
            "rest_port": 8080,
            "username": None,
            "debug": False,
        }
        return argparse.Namespace(**(base | overrides))

    async def test_missing_token_returns_one_and_never_starts_bot(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ):
        monkeypatch.setattr(
            telegram_module,
            "get_settings",
            lambda: SimpleNamespace(telegram_bot_token="", telegram_allowed_users="123"),
        )

        # The bot startup path begins by parsing the user whitelist; the
        # token check must bail out before any of that happens.
        parse_calls: list[str] = []
        monkeypatch.setattr(
            "tlon_client.telegram.reply_map.parse_allowed_users",
            lambda raw: parse_calls.append(raw),
        )

        result = await run_telegram(self._args())

        assert result == 1
        assert "TELEGRAM_BOT_TOKEN" in capsys.readouterr().out
        assert parse_calls == []

    async def test_empty_allowed_users_returns_one(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ):
        monkeypatch.setattr(
            telegram_module,
            "get_settings",
            lambda: SimpleNamespace(telegram_bot_token="token", telegram_allowed_users=""),
        )

        result = await run_telegram(self._args())

        assert result == 1
        assert "TELEGRAM_ALLOWED_USERS" in capsys.readouterr().out

    async def test_invalid_allowed_users_returns_one(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ):
        monkeypatch.setattr(
            telegram_module,
            "get_settings",
            lambda: SimpleNamespace(
                telegram_bot_token="token", telegram_allowed_users="not-a-number"
            ),
        )

        result = await run_telegram(self._args())

        assert result == 1
        assert "TELEGRAM_ALLOWED_USERS" in capsys.readouterr().out
