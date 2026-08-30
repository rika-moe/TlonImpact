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
    monkeypatch.setattr("tlon.base.settings.paths", p)
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


#: All LLM_* env keys Settings reads (ADR 0012 phase 2 rename; no OPENAI_*
#: aliases are honored).
_LLM_ENV_KEYS = (
    "LLM_BASE_URL",
    "LLM_API_KEY",
    "LLM_MODEL",
    "LLM_TEMPERATURE",
    "LLM_MAX_RETRIES",
    "LLM_BASE_DELAY",
)


class TestSettingsLLMEnv:
    """Settings reads the LLM_* variables (ADR 0012 phase 2)."""

    def _clear_llm_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for key in _LLM_ENV_KEYS:
            monkeypatch.delenv(key, raising=False)

    def test_reads_llm_env_vars(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("tlon.base.settings.paths", TlonPaths(home=tmp_path))
        monkeypatch.setenv("LLM_BASE_URL", "https://private.example/v1")
        monkeypatch.setenv("LLM_API_KEY", "sk-test")
        monkeypatch.setenv("LLM_MODEL", "deepseek/deepseek-chat")
        monkeypatch.setenv("LLM_TEMPERATURE", "0.5")
        monkeypatch.setenv("LLM_MAX_RETRIES", "5")
        monkeypatch.setenv("LLM_BASE_DELAY", "2.5")

        settings = Settings()

        assert settings.llm_base_url == "https://private.example/v1"
        assert settings.llm_api_key == "sk-test"
        assert settings.llm_model_name == "deepseek/deepseek-chat"
        assert settings.llm_temperature == 0.5
        assert settings.llm_max_retries == 5
        assert settings.llm_base_delay == 2.5

    def test_defaults(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("tlon.base.settings.paths", TlonPaths(home=tmp_path))
        self._clear_llm_env(monkeypatch)

        settings = Settings()

        assert settings.llm_base_url == "https://api.openai.com/v1"
        assert settings.llm_api_key == ""
        # provider/model form; litellm's routing table resolves the base_url
        assert settings.llm_model_name == "moonshot/kimi-k2.5"
        assert settings.llm_temperature == 1.0
        assert settings.llm_max_retries == 3
        assert settings.llm_base_delay == 1.0
