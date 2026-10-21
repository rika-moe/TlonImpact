"""Model inputs are frozen per root while the invocation ledger remains authoritative."""

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from tlon.agents.program_store import ProgramStore
from tlon.base.config import LLMConfig
from tlon.base.execution import ExecutionScope, bind_execution
from tlon.base.llm_client import LLMClient, LLMResponse
from tlon.operations.schema import InputError, RecoveryMismatchError


@pytest.fixture(params=[False, True], ids=["memory", "sqlite"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> ProgramStore:
    return ProgramStore(tmp_path / "program.sqlite" if request.param else None, build_id="test")


def test_frozen_context_is_first_wins_strict_and_isolated(store: ProgramStore) -> None:
    source = {"nested": [{"value": 1}]}
    result = store.freeze_model_context("root", "host", source)
    source["nested"][0]["value"] = 2
    result["nested"][0]["value"] = 3
    assert store.freeze_model_context("root", "host", {"replacement": True}) == {
        "nested": [{"value": 1}]
    }
    assert store.load_model_context("other", "host") is None
    with pytest.raises(InputError):
        store.freeze_model_context("root", "invalid", {"value": (1, 2)})
    store.build_id = "changed"
    with pytest.raises(RecoveryMismatchError):
        store.load_model_context("root", "host")


async def test_changed_configuration_uses_frozen_client_for_new_calls(store: ProgramStore) -> None:
    original = LLMConfig(model_name="openai/old", max_tokens=4, max_retries=1, base_delay=0.1)
    client = LLMClient(original)
    observed: list[tuple[str | None, int, int]] = []

    async def capture(self: LLMClient, *args: Any) -> LLMResponse:
        observed.append((self.config.model_name, self.max_retries, args[-1]))
        return LLMResponse("answer", usage={"completion_tokens": 1})

    from unittest.mock import patch

    with patch.object(LLMClient, "_chat_accounted", capture):
        with bind_execution(ExecutionScope(store, "root", 12, "output")):
            await client.chat("system", [{"role": "user", "content": "one"}])
        client.config = LLMConfig(model_name="openai/new", max_tokens=10, max_retries=9)
        with bind_execution(ExecutionScope(store, "root", 12, "output")):
            await client.chat("system", [{"role": "user", "content": "one"}])
            await client.chat("system", [{"role": "user", "content": "two"}])
    assert observed == [("openai/old", 1, 4), ("openai/old", 1, 4)]
    assert store.budget_usage("root")["used"] == 8
    assert client.config.model_name == "openai/new"


async def test_lookup_reads_authoritative_model_state_without_new_reservation(
    store: ProgramStore,
) -> None:
    execute = AsyncMock(return_value=LLMResponse("answer").to_dict())
    await ExecutionScope(store, "root", 10, "output").model_call({"messages": []}, 4, execute)
    found = store.lookup_model_path("root", ("model:0",))
    assert found is not None
    assert found["status"] == "replay"
    assert found["request"] == {"messages": []}
    found["response"]["content"] = "mutated"
    again = store.lookup_model_path("root", ("model:0",))
    assert again is not None and again["response"]["content"] == "answer"
    assert store.lookup_model_path("root", ("missing",)) is None
    assert store.budget_usage("root")["used"] == 4
    assert execute.await_count == 1


def test_entry_creation_freezes_contexts_before_first_model_call(store: ProgramStore) -> None:
    package = {"model_contexts": {"host": {"system_prompt": "original"}}}
    store.ensure_entry("root", "{}", "{}", package)
    package["model_contexts"]["host"]["system_prompt"] = "changed"
    store.ensure_entry("root", "{}", "{}", package)
    assert store.load_model_context("root", "host") == {"system_prompt": "original"}


async def test_lookup_preserves_unknown_reservation_and_rejected_refund(
    store: ProgramStore,
) -> None:
    from tlon.base.execution import ModelRequestRejectedError, UnknownModelOutcomeError

    unknown = ExecutionScope(store, "unknown", 10, "output")
    with pytest.raises(UnknownModelOutcomeError):
        await unknown.model_call({}, 4, AsyncMock(side_effect=OSError("lost")))
    unknown_result = store.lookup_model_path("unknown", ("model:0",))
    assert unknown_result is not None and unknown_result["status"] == "unknown"
    rejected = ExecutionScope(store, "rejected", 10, "output")
    with pytest.raises(ModelRequestRejectedError):
        await rejected.model_call(
            {}, 4, AsyncMock(side_effect=ModelRequestRejectedError("bad request"))
        )
    rejected_result = store.lookup_model_path("rejected", ("model:0",))
    assert rejected_result is not None and rejected_result["status"] == "rejected"
    assert store.budget_usage("unknown")["used"] == 4
    assert store.budget_usage("rejected")["used"] == 0
