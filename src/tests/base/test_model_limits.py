"""Capability, complete-context and execution-resource caps share one model boundary."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from tlon.agents.program_store import ProgramStore
from tlon.base.config import LLMConfig
from tlon.base.execution import BudgetExceededError, ExecutionScope, bind_execution
from tlon.base.llm_client import LLMClient
from tlon.base.model_limits import model_output_limit, resolve_model_limits


@pytest.mark.parametrize(
    "metadata",
    [{}, {"max_output_tokens": None}, {"max_output_tokens": 0}, {"max_output_tokens": "8192"}],
)
def test_unresolved_output_capability_has_no_fallback(
    monkeypatch: pytest.MonkeyPatch, metadata: dict
) -> None:
    monkeypatch.setattr("litellm.get_model_info", Mock(return_value=metadata))
    with pytest.raises(ValueError, match="max_output_tokens is unresolved"):
        resolve_model_limits(LLMConfig(model_name="openai/unknown", context_window=100000))


def test_metadata_failure_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("litellm.get_model_info", Mock(side_effect=OSError("unavailable")))
    with pytest.raises(ValueError, match="Cannot resolve model output capability"):
        resolve_model_limits(LLMConfig(model_name="openai/unknown"))


def test_output_capability_resolves_and_is_not_persisted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "litellm.get_model_info",
        Mock(return_value={"max_output_tokens": 32768, "max_input_tokens": 131072}),
    )
    client = LLMClient(LLMConfig(model_name="openai/current"))
    snapshot = client.configuration_snapshot()
    assert snapshot["max_output_tokens"] == 32768
    assert snapshot["context_window"] == 131072
    assert "max_output_tokens" not in LLMConfig.model_validate(snapshot).model_dump()
    assert "max_tokens" not in LLMConfig.model_fields


@pytest.mark.parametrize(
    "measure,input_tokens,remaining,expected",
    [("output", 90, 100, 10), ("output", 20, 5, 5), ("total", 20, 25, 5), ("output", 100, 100, 0)],
)
def test_output_limit_obeys_context_and_budget(
    measure: str, input_tokens: int, remaining: int, expected: int
) -> None:
    config = LLMConfig(max_output_tokens=80, context_window=100)
    assert (
        model_output_limit(config, input_tokens, remaining=remaining, measure=measure) == expected
    )


@pytest.mark.parametrize("measure,remaining", [("output", 0), ("total", 10)])
def test_exhausted_budget_is_not_context_pressure(measure: str, remaining: int) -> None:
    with pytest.raises(BudgetExceededError):
        model_output_limit(
            LLMConfig(max_output_tokens=100, context_window=1000),
            10,
            remaining=remaining,
            measure=measure,
        )


@pytest.mark.parametrize(
    "measure,budget,window,explicit,expected",
    [
        ("output", 50000, 100000, None, 32768),
        ("output", 7000, 100000, None, 7000),
        ("total", 7000, 100000, None, 6900),
        ("output", 50000, 6000, None, 5900),
        ("output", 50000, 100000, 12, 12),
    ],
)
async def test_real_provider_receives_resource_cap_and_replay_uses_frozen_capability(
    monkeypatch: pytest.MonkeyPatch,
    measure: str,
    budget: int,
    window: int,
    explicit: int | None,
    expected: int,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    counter = Mock(return_value=100)
    monkeypatch.setattr("litellm.token_counter", counter)
    provider = AsyncMock(
        return_value=SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="answer", tool_calls=None, reasoning_content=None
                    ),
                    finish_reason="stop",
                )
            ],
            usage=None,
        )
    )
    monkeypatch.setattr("litellm.acompletion", provider)
    client = LLMClient(
        LLMConfig(model_name="openai/current", max_output_tokens=32768, context_window=window)
    )
    store = ProgramStore(None, build_id="test")
    with bind_execution(ExecutionScope(store, "r", budget, measure)):
        result = await client.chat(
            "system", [{"role": "user", "content": "work"}], max_tokens=explicit
        )
    assert result.content == "answer"
    assert provider.await_args.kwargs["max_tokens"] == expected
    state = store.lookup_model_path("r", ("model:0",))
    assert state is not None
    original_request = state["request_json"]
    assert json.loads(original_request)["request"]["max_output_tokens"] == 32768
    client.config = LLMConfig(model_name="openai/changed")
    monkeypatch.setattr(
        "litellm.get_model_info", Mock(side_effect=AssertionError("frozen capability must win"))
    )
    counter.side_effect = AssertionError("committed request must not be re-estimated")
    with bind_execution(ExecutionScope(store, "r", budget, measure)):
        assert (
            await client.chat("system", [{"role": "user", "content": "work"}], max_tokens=explicit)
            == result
        )
    provider.assert_awaited_once()
    assert store.lookup_model_path("r", ("model:0",))["request_json"] == original_request


async def test_exhausted_budget_wins_over_context_pressure_before_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("litellm.token_counter", Mock(return_value=100))
    provider = AsyncMock()
    monkeypatch.setattr("litellm.acompletion", provider)
    client = LLMClient(
        LLMConfig(model_name="openai/test", max_output_tokens=32768, context_window=100)
    )
    store = ProgramStore(None, build_id="test")
    with (
        bind_execution(ExecutionScope(store, "r", 0, "output")),
        pytest.raises(BudgetExceededError),
    ):
        await client.chat("system", [])
    provider.assert_not_awaited()
