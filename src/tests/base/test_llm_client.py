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
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
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


class TestCacheBreakpoints:
    """Explicit prompt caching (Anthropic family): breakpoints are injected
    as cache_control content blocks; automatic-caching providers (OpenAI,
    DeepSeek, Moonshot, GLM, ...) get the messages byte-untouched."""

    def _messages(self) -> list[dict[str, Any]]:
        return [
            {"role": "user", "content": "m1"},
            {"role": "assistant", "content": "m2"},
            {"role": "user", "content": "m3"},
        ]

    async def _send(
        self, client: LLMClient, monkeypatch: pytest.MonkeyPatch, **kwargs: Any
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        acompletion = AsyncMock(return_value=_fake_litellm_response())
        monkeypatch.setattr("litellm.acompletion", acompletion)
        messages = self._messages()
        await client.chat(system_prompt="sys", messages=messages, **kwargs)
        return acompletion.await_args.kwargs["messages"], messages

    @staticmethod
    def _cached_block(text: str) -> list[dict[str, Any]]:
        return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]

    async def test_anthropic_marks_system_and_anchor(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _client("anthropic/claude-sonnet-4-5")

        sent, original = await self._send(client, monkeypatch, cache_prefix_len=2)

        # system breakpoint (index 0) — also covers the tool schemas in
        # Anthropic's tools → system → messages cache hierarchy
        assert sent[0]["content"] == self._cached_block("sys")
        # the anchor's last message: messages[1] → sent[2]
        assert sent[2]["content"] == self._cached_block("m2")
        # everything else untouched
        assert sent[1]["content"] == "m1"
        assert sent[3]["content"] == "m3"
        # the caller's list is never mutated — the journaled session
        # context stays marker-free
        assert all(isinstance(m["content"], str) for m in original)

    async def test_anthropic_without_prefix_len_marks_only_system(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _client("anthropic/claude-sonnet-4-5")

        sent, _ = await self._send(client, monkeypatch)

        assert sent[0]["content"] == self._cached_block("sys")
        assert [m["content"] for m in sent[1:]] == ["m1", "m2", "m3"]

    async def test_anthropic_out_of_range_prefix_len_marks_only_system(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _client("anthropic/claude-sonnet-4-5")

        # 4 > len(messages) = 3: no anchor breakpoint, system only
        sent, _ = await self._send(client, monkeypatch, cache_prefix_len=4)

        assert sent[0]["content"] == self._cached_block("sys")
        assert [m["content"] for m in sent[1:]] == ["m1", "m2", "m3"]

    @pytest.mark.parametrize(
        "model_name",
        ["openai/gpt-5", "deepseek/deepseek-chat", "moonshot/kimi-k2.6", "zhipu/glm-4.6"],
    )
    async def test_automatic_caching_providers_untouched(
        self, model_name: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        client = _client(model_name)

        sent, _ = await self._send(client, monkeypatch, cache_prefix_len=2)

        assert sent[0]["content"] == "sys"
        assert [m["content"] for m in sent[1:]] == ["m1", "m2", "m3"]


class TestProviderKeyRouting:
    """issue : a ``provider/model`` name first checks the provider's
    own env key (``{PROVIDER}_API_KEY``); the shared LLM_API_KEY (the
    config's auth_token) is the fallback. Bare names never look at
    provider env vars."""

    async def test_provider_env_key_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek")
        client = _client("deepseek/deepseek-chat")

        params = await _call_and_capture(client, monkeypatch)

        assert params["api_key"] == "sk-deepseek"

    async def test_provider_env_key_falls_back_to_shared_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        client = _client("deepseek/deepseek-chat")

        params = await _call_and_capture(client, monkeypatch)

        assert params["api_key"] == "sk-test"

    async def test_provider_name_dashes_become_underscores(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AZURE_OPENAI_API_KEY", "sk-azure")
        client = _client("azure-openai/gpt-5")

        params = await _call_and_capture(client, monkeypatch)

        assert params["api_key"] == "sk-azure"

    async def test_bare_model_ignores_provider_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-env")
        client = _client("kimi-k2.5", base_url="https://api.moonshot.cn/v1")

        params = await _call_and_capture(client, monkeypatch)

        assert params["model"] == "openai/kimi-k2.5"
        assert params["api_key"] == "sk-test"
