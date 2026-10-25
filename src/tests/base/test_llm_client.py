"""Tests for LLMClient's litellm routing (ADR 0012 phase 2 / ADR 0016).

The model is always ``provider/model`` form, passed to litellm as-is (its
routing table resolves the provider's base_url); the API key is never
passed explicitly — litellm reads ``{PROVIDER}_API_KEY`` natively, and a
preflight check fails fast (no retry) when the model is malformed or the
key env var is missing. A member-level ``api_base`` overrides the
registry endpoint for private deployments / proxies.
"""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from tlon.base.config import LLMConfig
from tlon.base.llm_client import LLMClient, usage_total_tokens


def _fake_litellm_response() -> SimpleNamespace:
    message = SimpleNamespace(content="hi", tool_calls=None, reasoning_content=None)
    choice = SimpleNamespace(message=message, finish_reason="stop")
    usage = SimpleNamespace(model_dump=lambda: {"prompt_tokens": 1, "completion_tokens": 1})
    return SimpleNamespace(choices=[choice], usage=usage)


def _client(model_name: str, **overrides: Any) -> LLMClient:
    config = LLMConfig(
        model_name=model_name,
        **{"max_retries": 1, "max_output_tokens": 8192, "context_window": 128000, **overrides},
    )
    return LLMClient(config)


async def _call_and_capture(client: LLMClient, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    acompletion = AsyncMock(return_value=_fake_litellm_response())
    monkeypatch.setattr("litellm.acompletion", acompletion)
    response = await client.chat(system_prompt="sys", messages=[{"role": "user", "content": "hi"}])
    assert response.content == "hi"
    acompletion.assert_awaited_once()
    return acompletion.await_args.kwargs


class TestProviderModelRouting:
    async def test_provider_slash_model_passed_as_is(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """provider/model goes to litellm untouched; no api_base override —
        litellm's routing table resolves the provider's endpoint. The key
        is never passed: litellm reads {PROVIDER}_API_KEY natively (ADR
        0016)."""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek")
        client = _client("deepseek/deepseek-chat", api_base=None)

        params = await _call_and_capture(client, monkeypatch)

        assert params["model"] == "deepseek/deepseek-chat"
        assert params["api_base"] is None
        assert "api_key" not in params
        assert params["num_retries"] == 0

    async def test_member_api_base_overrides_registry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Member-level api_base (ADR 0016): private deployment / proxy /
        OpenAI-compatible endpoint."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
        client = _client("openai/kimi-k2.5", api_base="https://api.moonshot.cn/v1")

        params = await _call_and_capture(client, monkeypatch)

        assert params["model"] == "openai/kimi-k2.5"
        assert params["api_base"] == "https://api.moonshot.cn/v1"

    async def test_temperature_never_sent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Sampling is not configured anywhere (ADR 0016 follow-up):
        requests never carry temperature — the provider default applies."""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek")
        client = _client("deepseek/deepseek-chat")

        params = await _call_and_capture(client, monkeypatch)

        assert "temperature" not in params


class TestPreflight:
    """ADR 0016 preflight: configuration errors raise immediately, outside
    the retry loop — retrying can never fix them."""

    async def test_bare_model_name_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        acompletion = AsyncMock(return_value=_fake_litellm_response())
        monkeypatch.setattr("litellm.acompletion", acompletion)
        client = _client("kimi-k2.5")

        with pytest.raises(ValueError, match="provider/model"):
            await client.chat(system_prompt="sys", messages=[{"role": "user", "content": "hi"}])
        acompletion.assert_not_called()

    async def test_missing_provider_key_rejected_with_var_name(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        acompletion = AsyncMock(return_value=_fake_litellm_response())
        monkeypatch.setattr("litellm.acompletion", acompletion)
        client = _client("deepseek/deepseek-chat", max_retries=3)

        with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
            await client.chat(system_prompt="sys", messages=[{"role": "user", "content": "hi"}])
        # no retry: the preflight raise never reaches litellm
        acompletion.assert_not_called()

    async def test_provider_name_dashes_become_underscores(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
        client = _client("azure-openai/gpt-5")

        with pytest.raises(ValueError, match="AZURE_OPENAI_API_KEY"):
            await client.chat(system_prompt="sys", messages=[{"role": "user", "content": "hi"}])

    async def test_none_model_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _client(None)  # type: ignore[arg-type]

        with pytest.raises(ValueError, match="provider/model"):
            await client.chat(system_prompt="sys", messages=[{"role": "user", "content": "hi"}])


class TestUsageTotalTokens:
    """``usage_total_tokens`` (ADR 0023): the session-budget metering
    rule — total_tokens wins, prompt+completion is the fallback, None
    counts as zero."""

    def test_total_tokens_wins(self) -> None:
        usage = {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 42}
        assert usage_total_tokens(usage) == 42

    def test_prompt_plus_completion_fallback(self) -> None:
        assert usage_total_tokens({"prompt_tokens": 100, "completion_tokens": 50}) == 150

    def test_none_and_empty_count_zero(self) -> None:
        assert usage_total_tokens(None) == 0
        assert usage_total_tokens({}) == 0


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

    async def test_anthropic_marks_system_and_anchor(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-anthropic")
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
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-anthropic")
        client = _client("anthropic/claude-sonnet-4-5")

        sent, _ = await self._send(client, monkeypatch)

        assert sent[0]["content"] == self._cached_block("sys")
        assert [m["content"] for m in sent[1:]] == ["m1", "m2", "m3"]

    async def test_anthropic_out_of_range_prefix_len_marks_only_system(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-anthropic")
        client = _client("anthropic/claude-sonnet-4-5")

        # 4 > len(messages) = 3: no anchor breakpoint, system only
        sent, _ = await self._send(client, monkeypatch, cache_prefix_len=4)

        assert sent[0]["content"] == self._cached_block("sys")
        assert [m["content"] for m in sent[1:]] == ["m1", "m2", "m3"]

    @pytest.mark.parametrize(
        ("model_name", "key_env"),
        [
            ("openai/gpt-5", "OPENAI_API_KEY"),
            ("deepseek/deepseek-chat", "DEEPSEEK_API_KEY"),
            ("moonshot/kimi-k2.6", "MOONSHOT_API_KEY"),
            ("zhipu/glm-4.6", "ZHIPU_API_KEY"),
        ],
    )
    async def test_automatic_caching_providers_untouched(
        self, model_name: str, key_env: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(key_env, "sk-test")
        client = _client(model_name)

        sent, _ = await self._send(client, monkeypatch, cache_prefix_len=2)

        assert sent[0]["content"] == "sys"
        assert [m["content"] for m in sent[1:]] == ["m1", "m2", "m3"]


async def test_structured_json_mode_reaches_litellm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake")
    provider = AsyncMock(return_value=_fake_litellm_response())
    monkeypatch.setattr("litellm.acompletion", provider)
    await _client("deepseek/deepseek-chat").chat(
        "Return JSON", [], response_format={"type": "json_object"}
    )
    assert provider.await_args.kwargs["response_format"] == {"type": "json_object"}


async def test_response_schema_is_part_of_durable_model_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tlon.agents.program_store import ProgramStore
    from tlon.base.execution import ExecutionScope, bind_execution
    from tlon.operations.schema import RecoveryMismatchError

    monkeypatch.setenv("OPENAI_API_KEY", "fake")
    provider = AsyncMock(return_value=_fake_litellm_response())
    monkeypatch.setattr("litellm.acompletion", provider)
    store = ProgramStore(None, build_id="schema")
    client = _client("openai/test")
    original = {"type": "json_object"}
    for _ in range(2):
        with bind_execution(ExecutionScope(store, "root", 10000, "output")):
            await client.chat("Return JSON", [], response_format=original)
    with (
        bind_execution(ExecutionScope(store, "root", 10000, "output")),
        pytest.raises(RecoveryMismatchError),
    ):
        await client.chat("Return JSON", [], response_format={"type": "text"})
    assert provider.await_count == 1
    assert store.budget_usage("root")["used"] == 1
