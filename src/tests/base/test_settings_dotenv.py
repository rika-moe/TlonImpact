"""Tests for _load_dotenv_files semantics.

The env config lives in the data directory — ``{TLON_HOME}/.env``
(default ``~/.tlon/.env``) — and real environment variables always win
(the .env only fills in variables that are not already set,
``override=False``). A .env in the checkout or cwd is never picked up.
"""

import os
from pathlib import Path

import pytest

from tlon.base.paths import TlonPaths
from tlon.base.settings import Settings, _load_dotenv_files


def _fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, with_env: bool) -> TlonPaths:
    """Point the paths singleton's tlon_home at tmp_path so the .env lookup
    resolves there instead of the real ~/.tlon."""
    p = TlonPaths(home=tmp_path)
    if with_env:
        (tmp_path / ".env").write_text(
            "TLON_TEST_DOTENV_KEY=from_dotenv\n"
            "TLON_TEST_DOTENV_FALLBACK=from_dotenv\n",
            encoding="utf-8",
        )
    monkeypatch.setattr("tlon.base.settings._paths", p)
    return p


class TestLoadDotenvFiles:
    def test_real_env_var_not_overridden_by_dotenv(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A real environment variable beats the {TLON_HOME}/.env value."""
        _fake_home(tmp_path, monkeypatch, with_env=True)
        monkeypatch.setenv("TLON_TEST_DOTENV_KEY", "from_environment")
        monkeypatch.delenv("TLON_TEST_DOTENV_FALLBACK", raising=False)

        _load_dotenv_files()

        assert os.environ["TLON_TEST_DOTENV_KEY"] == "from_environment"

    def test_dotenv_fills_missing_vars(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The {TLON_HOME}/.env supplies variables absent from the environment."""
        _fake_home(tmp_path, monkeypatch, with_env=True)
        monkeypatch.delenv("TLON_TEST_DOTENV_KEY", raising=False)
        monkeypatch.delenv("TLON_TEST_DOTENV_FALLBACK", raising=False)

        _load_dotenv_files()

        assert os.environ["TLON_TEST_DOTENV_FALLBACK"] == "from_dotenv"

    def test_cwd_and_checkout_dotenv_are_ignored(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A .env in the cwd or next to the checkout is never picked up —
        only {TLON_HOME}/.env counts."""
        _fake_home(tmp_path, monkeypatch, with_env=False)
        cwd = tmp_path / "elsewhere"
        cwd.mkdir()
        (cwd / ".env").write_text("TLON_TEST_DOTENV_KEY=from_cwd\n", encoding="utf-8")
        monkeypatch.chdir(cwd)
        monkeypatch.delenv("TLON_TEST_DOTENV_KEY", raising=False)

        _load_dotenv_files()

        assert "TLON_TEST_DOTENV_KEY" not in os.environ


class TestSettingsGitHubEnv:
    """Settings reads the GitHub webhook ingress variables (docs/design/mail.md)."""

    def test_reads_github_env_vars(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("tlon.base.settings._paths", TlonPaths(home=tmp_path))
        monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "whsec")
        monkeypatch.setenv("GITHUB_RECIPIENT", "human:alice")

        settings = Settings()

        assert settings.github_webhook_secret == "whsec"
        assert settings.github_recipient == "human:alice"

    def test_defaults_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Empty secret = the /webhooks/github route is not registered."""
        monkeypatch.setattr("tlon.base.settings._paths", TlonPaths(home=tmp_path))
        monkeypatch.delenv("GITHUB_WEBHOOK_SECRET", raising=False)
        monkeypatch.delenv("GITHUB_RECIPIENT", raising=False)

        settings = Settings()

        assert settings.github_webhook_secret == ""
        assert settings.github_recipient == ""
