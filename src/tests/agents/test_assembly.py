"""Tests for _build_llm_config's context_window resolution (ADR 0012 phase 2).

Precedence: explicit agent.yaml value > litellm model registry
(``get_model_info().max_input_tokens``) > DEFAULT_CONTEXT_WINDOW. The built
config always carries a resolved int.
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
