"""Tests for load_process_env / Settings.from_env semantics.

The env config lives in the data directory — ``{TLON_HOME}/.env``
(default ``~/.tlon/.env``) — and real environment variables always win
(the .env only fills in variables that are not already set,
``override=False``). A .env in the checkout or cwd is never picked up.

The .env load is an explicit entry-point action taking the entry's own
``TlonPaths`` — no module-level state, no implicit paths resolution.
"""

import os
from pathlib import Path

import pytest

from tlon.base.paths import TlonPaths
from tlon.base.settings import Settings, load_process_env


def _home(tmp_path: Path, *, with_env: bool) -> TlonPaths:
    """An isolated TlonPaths rooted at tmp_path, optionally with a .env."""
    p = TlonPaths(home=tmp_path)
    if with_env:
        (tmp_path / ".env").write_text(
            "TLON_TEST_DOTENV_KEY=from_dotenv\nTLON_TEST_DOTENV_FALLBACK=from_dotenv\n",
            encoding="utf-8",
        )
    return p


class TestLoadProcessEnv:
    def test_real_env_var_not_overridden_by_dotenv(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A real environment variable beats the {TLON_HOME}/.env value."""
        paths = _home(tmp_path, with_env=True)
        monkeypatch.setenv("TLON_TEST_DOTENV_KEY", "from_environment")
        monkeypatch.delenv("TLON_TEST_DOTENV_FALLBACK", raising=False)

        load_process_env(paths)

        assert os.environ["TLON_TEST_DOTENV_KEY"] == "from_environment"

    def test_dotenv_fills_missing_vars(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The {TLON_HOME}/.env supplies variables absent from the environment."""
        paths = _home(tmp_path, with_env=True)
        monkeypatch.delenv("TLON_TEST_DOTENV_KEY", raising=False)
        monkeypatch.delenv("TLON_TEST_DOTENV_FALLBACK", raising=False)

        load_process_env(paths)

        assert os.environ["TLON_TEST_DOTENV_FALLBACK"] == "from_dotenv"

    def test_cwd_and_checkout_dotenv_are_ignored(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A .env in the cwd or next to the checkout is never picked up —
        only {TLON_HOME}/.env counts."""
        paths = _home(tmp_path, with_env=False)
        cwd = tmp_path / "elsewhere"
        cwd.mkdir()
        (cwd / ".env").write_text("TLON_TEST_DOTENV_KEY=from_cwd\n", encoding="utf-8")
        monkeypatch.chdir(cwd)
        monkeypatch.delenv("TLON_TEST_DOTENV_KEY", raising=False)

        load_process_env(paths)

        assert "TLON_TEST_DOTENV_KEY" not in os.environ


class TestSettingsFromEnv:
    """Settings.from_env snapshots the GitHub webhook ingress variables
    (docs/design/mail.md)."""

    def test_reads_github_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "whsec")
        monkeypatch.setenv("GITHUB_RECIPIENT", "human:alice")

        settings = Settings.from_env()

        assert settings.github_webhook_secret == "whsec"
        assert settings.github_recipient == "human:alice"

    def test_defaults_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Empty secret = the /webhooks/github route is not registered."""
        monkeypatch.delenv("GITHUB_WEBHOOK_SECRET", raising=False)
        monkeypatch.delenv("GITHUB_RECIPIENT", raising=False)

        settings = Settings.from_env()

        assert settings.github_webhook_secret == ""
        assert settings.github_recipient == ""


def test_settings_import_has_no_filesystem_side_effect(tmp_path: Path) -> None:
    """Importing/reading settings never resolves $TLON_HOME or creates
    directories — the module holds no module-level paths instance."""
    import tlon.base.settings as settings_module

    assert not hasattr(settings_module, "_paths")
    assert not hasattr(settings_module, "_settings")
    assert not hasattr(settings_module, "get_settings")
