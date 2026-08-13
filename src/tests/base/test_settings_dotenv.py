"""Tests for _load_dotenv_files semantics (issue ).

The repo-root .env is loaded explicitly (never cwd-relative), and real
environment variables always win — the .env only fills in variables that
are not already set (override=False).
"""

import os
from pathlib import Path

import pytest

from tlon.base.settings import _load_dotenv_files


def _fake_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, with_env: bool) -> Path:
    """Point settings.__file__ at a fake package so the repo-root .env lookup
    resolves inside tmp_path instead of the real repository."""
    project_dir = tmp_path / "project"
    pkg_dir = project_dir / "a" / "b" / "c"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "settings.py").touch()
    if with_env:
        (project_dir / ".env").write_text(
            "TLON_TEST_DOTENV_KEY=from_dotenv\n"
            "TLON_TEST_DOTENV_FALLBACK=from_dotenv\n",
            encoding="utf-8",
        )
    monkeypatch.setattr("tlon.base.settings.__file__", str(pkg_dir / "settings.py"))
    return project_dir


class TestLoadDotenvFiles:
    def test_real_env_var_not_overridden_by_dotenv(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A real environment variable beats the repo .env value."""
        _fake_project(tmp_path, monkeypatch, with_env=True)
        monkeypatch.setenv("TLON_TEST_DOTENV_KEY", "from_environment")
        monkeypatch.delenv("TLON_TEST_DOTENV_FALLBACK", raising=False)

        _load_dotenv_files()

        assert os.environ["TLON_TEST_DOTENV_KEY"] == "from_environment"

    def test_dotenv_fills_missing_vars(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The repo .env still supplies variables absent from the environment."""
        _fake_project(tmp_path, monkeypatch, with_env=True)
        monkeypatch.delenv("TLON_TEST_DOTENV_KEY", raising=False)
        monkeypatch.delenv("TLON_TEST_DOTENV_FALLBACK", raising=False)

        _load_dotenv_files()

        assert os.environ["TLON_TEST_DOTENV_FALLBACK"] == "from_dotenv"

    def test_cwd_dotenv_is_ignored(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A .env in the current working directory is never picked up."""
        _fake_project(tmp_path, monkeypatch, with_env=False)
        cwd = tmp_path / "elsewhere"
        cwd.mkdir()
        (cwd / ".env").write_text("TLON_TEST_DOTENV_KEY=from_cwd\n", encoding="utf-8")
        monkeypatch.chdir(cwd)
        monkeypatch.delenv("TLON_TEST_DOTENV_KEY", raising=False)

        _load_dotenv_files()

        assert "TLON_TEST_DOTENV_KEY" not in os.environ
