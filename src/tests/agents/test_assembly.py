"""Tests for _build_llm_config's merge and context_window resolution.

Model precedence (issue ): explicit agent.yaml ``model_name`` >
settings ``LLM_MODEL``. Context window precedence (ADR 0012 phase 2):
explicit agent.yaml value > litellm model registry
(``get_model_info().max_input_tokens``) > DEFAULT_CONTEXT_WINDOW. The
built config always carries a resolved int and a non-None model_name.
"""

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from tlon.agents.assembly import _build_llm_config
from tlon.base.config import DEFAULT_CONTEXT_WINDOW, LLMConfig


def _settings(**overrides: Any) -> SimpleNamespace:
    defaults: dict[str, Any] = {
        "llm_model_name": "moonshot/kimi-k2.5",
        "llm_temperature": 1.0,
        "llm_base_url": "https://api.openai.com/v1",
        "llm_api_key": "sk-test",
        "llm_max_retries": 3,
        "llm_base_delay": 1.0,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestContextWindowResolution:
    def test_yaml_explicit_value_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        get_model_info = MagicMock(return_value={"max_input_tokens": 131072})
        monkeypatch.setattr("litellm.get_model_info", get_model_info)
        file_config = LLMConfig(context_window=99999)

        built = _build_llm_config(file_config, _settings())

        assert built.context_window == 99999
        get_model_info.assert_not_called()

    def test_registry_hit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "litellm.get_model_info", MagicMock(return_value={"max_input_tokens": 131072})
        )

        built = _build_llm_config(LLMConfig(), _settings())

        assert built.context_window == 131072

    def test_registry_miss_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "litellm.get_model_info",
            MagicMock(side_effect=Exception("model not in registry")),
        )

        built = _build_llm_config(LLMConfig(), _settings())

        assert built.context_window == DEFAULT_CONTEXT_WINDOW

    def test_registry_entry_without_max_input_tokens_falls_back(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "litellm.get_model_info", MagicMock(return_value={"max_input_tokens": None})
        )

        built = _build_llm_config(LLMConfig(), _settings())

        assert built.context_window == DEFAULT_CONTEXT_WINDOW

    def test_none_file_config_resolves(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "litellm.get_model_info", MagicMock(return_value={"max_input_tokens": 64000})
        )

        built = _build_llm_config(None, _settings())

        assert built.context_window == 64000


class TestModelNameMerge:
    """issue : an explicit agent.yaml model_name wins; settings
    (``LLM_MODEL``) is the fallback. The built config is never None."""

    def test_file_explicit_model_overrides_settings(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "litellm.get_model_info", MagicMock(return_value={"max_input_tokens": 64000})
        )
        file_config = LLMConfig(model_name="deepseek/deepseek-chat")

        built = _build_llm_config(file_config, _settings(llm_model_name="moonshot/kimi-k2.5"))

        assert built.model_name == "deepseek/deepseek-chat"

    def test_context_window_resolves_against_file_model(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        get_model_info = MagicMock(return_value={"max_input_tokens": 64000})
        monkeypatch.setattr("litellm.get_model_info", get_model_info)
        file_config = LLMConfig(model_name="deepseek/deepseek-chat")

        _build_llm_config(file_config, _settings(llm_model_name="moonshot/kimi-k2.5"))

        assert get_model_info.call_args.args[0] == "deepseek/deepseek-chat"

    def test_llm_section_without_model_falls_back_to_settings(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """model_name's default is None = undeclared: a file llm section
        that pins only temperature still falls back to settings."""
        monkeypatch.setattr(
            "litellm.get_model_info", MagicMock(return_value={"max_input_tokens": 64000})
        )
        file_config = LLMConfig(max_tokens=2048)

        built = _build_llm_config(file_config, _settings())

        assert built.model_name == "moonshot/kimi-k2.5"
        assert built.max_tokens == 2048

    def test_no_llm_section_uses_settings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "litellm.get_model_info", MagicMock(return_value={"max_input_tokens": 64000})
        )

        built = _build_llm_config(None, _settings())

        assert built.model_name == "moonshot/kimi-k2.5"
