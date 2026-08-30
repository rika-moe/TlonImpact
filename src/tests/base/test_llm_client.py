"""Tests for LLMClient's litellm routing (ADR 0012 phase 2).

A ``provider/model`` name is passed to litellm as-is (its routing table
resolves the provider's base_url); a bare model name is the escape hatch —
forced through the openai provider against the configured api_base.
"""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from tlon.base.config import LLMConfig
from tlon.base.llm_client import LLMClient


def _fake_litellm_response() -> SimpleNamespace:
    message = SimpleNamespace(content="hi", tool_calls=None, reasoning_content=None)
    choice = SimpleNamespace(message=message, finish_reason="stop")
    usage = SimpleNamespace(model_dump=lambda: {"prompt_tokens": 1, "completion_tokens": 1})
    return SimpleNamespace(choices=[choice], usage=usage)


def _client(model_name: str, **overrides: Any) -> LLMClient:
    config = LLMConfig(
        model_name=model_name,
        auth_token="sk-test",
        max_retries=1,
        **overrides,
    )
    return LLMClient(config)


async def _call_and_capture(
    client: LLMClient, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any]:
    acompletion = AsyncMock(return_value=_fake_litellm_response())
    monkeypatch.setattr("litellm.acompletion", acompletion)
    response = await client.chat(system_prompt="sys", messages=[{"role": "user", "content": "hi"}])
    assert response.content == "hi"
    acompletion.assert_awaited_once()
    return acompletion.await_args.kwargs


class TestProviderModelRouting:
    async def test_provider_slash_model_passed_as_is(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """provider/model goes to litellm untouched; no api_base override —
        litellm's routing table resolves the provider's endpoint."""
        client = _client("deepseek/deepseek-chat", base_url="https://ignored.example/v1")

        params = await _call_and_capture(client, monkeypatch)

        assert params["model"] == "deepseek/deepseek-chat"
        assert params["api_base"] is None
        # single LLM_API_KEY is forwarded explicitly, whatever the provider
        assert params["api_key"] == "sk-test"
        assert params["num_retries"] == 0

    async def test_bare_model_gets_openai_prefix(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Bare model name = escape hatch: openai/ prefix + configured
        api_base (private deployment / OpenAI-compatible endpoint)."""
        client = _client("kimi-k2.5", base_url="https://api.moonshot.cn/v1")

        params = await _call_and_capture(client, monkeypatch)

        assert params["model"] == "openai/kimi-k2.5"
        assert params["api_base"] == "https://api.moonshot.cn/v1"

    async def test_bare_model_without_base_url_passes_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _client("kimi-k2.5", base_url="")

        params = await _call_and_capture(client, monkeypatch)

        assert params["model"] == "openai/kimi-k2.5"
        assert params["api_base"] is None
