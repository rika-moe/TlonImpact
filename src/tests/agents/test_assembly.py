"""Tests for _build_llm_config's member-level resolution (ADR 0016).

There is no process-level fallback: an agent.yaml without a declared
``llm.model_name`` is a hard error naming the member. Context
window precedence (ADR 0012 phase 2): explicit agent.yaml value >
litellm model registry (``get_model_info().max_input_tokens``) >
DEFAULT_CONTEXT_WINDOW. The built config always carries a resolved int
and a non-None model_name.
"""

from unittest.mock import MagicMock

import pytest

from tlon.agents.assembly import _build_llm_config
from tlon.base.config import DEFAULT_CONTEXT_WINDOW, LLMConfig

_MODEL = "moonshot/kimi-k2.5"


def _file(**overrides: object) -> LLMConfig:
    return LLMConfig(model_name=_MODEL, **overrides)  # type: ignore[arg-type]


class TestContextWindowResolution:
    def test_yaml_explicit_value_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        get_model_info = MagicMock(return_value={"max_input_tokens": 131072})
        monkeypatch.setattr("litellm.get_model_info", get_model_info)
        file_config = _file(context_window=99999)

        built = _build_llm_config(file_config, "member-1")

        assert built.context_window == 99999
        get_model_info.assert_not_called()

    def test_registry_hit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "litellm.get_model_info", MagicMock(return_value={"max_input_tokens": 131072})
        )

        built = _build_llm_config(_file(), "member-1")

        assert built.context_window == 131072

    def test_registry_miss_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "litellm.get_model_info",
            MagicMock(side_effect=Exception("model not in registry")),
        )

        built = _build_llm_config(_file(), "member-1")

        assert built.context_window == DEFAULT_CONTEXT_WINDOW

    def test_registry_entry_without_max_input_tokens_falls_back(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "litellm.get_model_info", MagicMock(return_value={"max_input_tokens": None})
        )

        built = _build_llm_config(_file(), "member-1")

        assert built.context_window == DEFAULT_CONTEXT_WINDOW


class TestModelNameRequired:
    """ADR 0016: no declared ``llm.model_name`` is a hard assembly error
    naming the member — no process-level fallback exists."""

    def test_missing_llm_section_raises(self) -> None:
        with pytest.raises(ValueError, match="member-1"):
            _build_llm_config(None, "member-1")

    def test_llm_section_without_model_raises(self) -> None:
        with pytest.raises(ValueError, match="llm.model_name"):
            _build_llm_config(LLMConfig(max_tokens=2048), "member-1")

    def test_declared_model_passes_through(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        get_model_info = MagicMock(return_value={"max_input_tokens": 64000})
        monkeypatch.setattr("litellm.get_model_info", get_model_info)
        file_config = LLMConfig(model_name="deepseek/deepseek-chat")

        built = _build_llm_config(file_config, "member-1")

        assert built.model_name == "deepseek/deepseek-chat"
        # the context window resolves against the declared model
        assert get_model_info.call_args.args[0] == "deepseek/deepseek-chat"


class TestFileValuesTakeEffect:
    """ADR 0016: the file's per-member knobs are the only source."""

    def test_per_member_knobs_and_api_base_pass_through(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "litellm.get_model_info", MagicMock(return_value={"max_input_tokens": 64000})
        )

        built = _build_llm_config(
            _file(max_tokens=2048, top_p=0.5, api_base="https://proxy.example/v1"),
            "member-1",
        )

        assert built.max_tokens == 2048
        assert built.top_p == 0.5
        assert built.api_base == "https://proxy.example/v1"
