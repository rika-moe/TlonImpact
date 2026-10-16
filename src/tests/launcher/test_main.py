"""Tests for the tlon CLI entry point (tlon_launcher.main)."""

import argparse
import sys
from typing import Any
from unittest.mock import AsyncMock

import pytest

from tlon.base.version import VERSION
from tlon_launcher import main as main_module
from tlon_launcher.main import create_parser, main, main_async


class TestCreateParser:
    def test_no_command(self):
        args = create_parser().parse_args([])

        assert args.command is None
        assert args.debug is False

    def test_version_exits_zero(self, capsys: pytest.CaptureFixture[str]):
        with pytest.raises(SystemExit) as exc_info:
            create_parser().parse_args(["--version"])

        assert exc_info.value.code == 0
        assert VERSION in capsys.readouterr().out

    def test_dream_defaults(self):
        args = create_parser().parse_args(["dream"])

        assert args.command == "dream"
        assert args.host == "127.0.0.1"
        assert args.rest_port == 8080
        assert args.debug is False

    def test_dream_lucid_sets_debug(self):
        for flag in ("--lucid", "-l"):
            args = create_parser().parse_args(["dream", flag])
            assert args.debug is True

    def test_dream_debug_hidden_alias(self):
        args = create_parser().parse_args(["dream", "--debug"])

        assert args.debug is True

    def test_dream_custom_host_and_port(self):
        args = create_parser().parse_args(["dream", "--host", "0.0.0.0", "--rest-port", "9090"])

        assert args.host == "0.0.0.0"
        assert args.rest_port == 9090

    def test_ops_defaults(self):
        args = create_parser().parse_args(["ops"])

        assert args.command == "ops"
        assert args.host == "127.0.0.1"
        assert args.rest_port == 8080
        assert args.username is None
        assert args.debug is False

    def test_ops_username(self):
        for flag in ("--username", "-u"):
            args = create_parser().parse_args(["ops", flag, "alice"])
            assert args.username == "alice"

    def test_ops_lucid_sets_debug(self):
        args = create_parser().parse_args(["ops", "--lucid"])

        assert args.debug is True

    def test_telegram_defaults(self):
        args = create_parser().parse_args(["telegram"])

        assert args.command == "telegram"
        assert args.host == "127.0.0.1"
        assert args.rest_port == 8080
        assert args.username is None

    def test_telegram_has_no_lucid_flag(self):
        with pytest.raises(SystemExit) as exc_info:
            create_parser().parse_args(["telegram", "--lucid"])

        assert exc_info.value.code == 2

    def test_global_debug_flag(self):
        args = create_parser().parse_args(["--debug"])

        assert args.debug is True


class TestMainAsync:
    def _args(self, command: str | None, **extra: Any) -> argparse.Namespace:
        base: dict[str, Any] = {"command": command, "debug": False}
        return argparse.Namespace(**(base | extra))

    def _patch_infra(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(main_module, "configure_logging", lambda **kwargs: None)
        monkeypatch.setattr(
            main_module,
            "get_settings",
            lambda: argparse.Namespace(debug=False, log_level="INFO"),
        )

    @pytest.mark.parametrize("command", ["dream", "ops", "telegram"])
    async def test_routes_to_command(self, monkeypatch: pytest.MonkeyPatch, command: str) -> None:
        self._patch_infra(monkeypatch)
        runners: dict[str, AsyncMock] = {}
        for name in ("run_dream", "run_ops", "run_telegram"):
            runner = AsyncMock(return_value=0)
            runners[name] = runner
            monkeypatch.setattr(main_module, name, runner)

        args = self._args(command)
        result = await main_async(args)

        assert result == 0
        runners[f"run_{command}"].assert_awaited_once_with(args)
        for name, runner in runners.items():
            if name != f"run_{command}":
                runner.assert_not_awaited()

    async def test_exit_code_passthrough(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch_infra(monkeypatch)
        monkeypatch.setattr(main_module, "run_dream", AsyncMock(return_value=3))

        result = await main_async(self._args("dream"))

        assert result == 3

    async def test_no_command_prints_help_and_returns_zero(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._patch_infra(monkeypatch)

        result = await main_async(self._args(None))

        assert result == 0
        assert "Commands:" in capsys.readouterr().out

    async def test_debug_enables_debug_log_level(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[dict[str, Any]] = []
        monkeypatch.setattr(main_module, "configure_logging", lambda **kwargs: calls.append(kwargs))
        monkeypatch.setattr(
            main_module,
            "get_settings",
            lambda: argparse.Namespace(debug=False, log_level="INFO"),
        )
        monkeypatch.setattr(main_module, "run_dream", AsyncMock(return_value=0))

        await main_async(self._args("dream", debug=True))

        assert calls == [{"log_level": "DEBUG", "debug": True}]

    async def test_log_level_comes_from_settings_when_not_debug(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[dict[str, Any]] = []
        monkeypatch.setattr(main_module, "configure_logging", lambda **kwargs: calls.append(kwargs))
        monkeypatch.setattr(
            main_module,
            "get_settings",
            lambda: argparse.Namespace(debug=False, log_level="WARNING"),
        )
        monkeypatch.setattr(main_module, "run_dream", AsyncMock(return_value=0))

        await main_async(self._args("dream"))

        assert calls == [{"log_level": "WARNING", "debug": False}]


class TestMain:
    def test_returns_main_async_exit_code(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "argv", ["tlon"])
        monkeypatch.setattr(main_module, "main_async", AsyncMock(return_value=42))

        assert main() == 42

    def test_keyboard_interrupt_returns_130(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(sys, "argv", ["tlon"])
        monkeypatch.setattr(main_module, "main_async", AsyncMock(side_effect=KeyboardInterrupt))

        assert main() == 130
        assert "入侵被中断" in capsys.readouterr().out
