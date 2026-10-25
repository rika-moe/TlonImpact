"""Source-defined boundary handoff reuses the root's committed model receipt and budget."""

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from tlon.agents.program_driver import ProgramDriver
from tlon.agents.program_package import program_builtins, program_module_sources
from tlon.agents.program_store import ProgramStore
from tlon.base.config import LLMConfig
from tlon.base.llm_client import LLMClient, LLMResponse
from tlon.base.paths import TlonPaths
from tlon.operations.core import CallContext, OperationExecutor, OperationRegistry
from tlon.operations.dsl import DSLAdapter
from tlon.operations.invocation import Invocation, InvocationOutcome
from tlon.operations.model import make_model_operations
from tlosure.edn import Keyword
from tlosure.modules import ModulePackage


async def test_handoff_recovers_inner_commit_without_rewriting_history_or_new_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ProgramStore(tmp_path / "run.sqlite", build_id="test")
    client = LLMClient(
        LLMConfig(model_name="openai/test", max_output_tokens=8192, context_window=128000),
        agent_id="a",
        tlon_paths=TlonPaths(home=tmp_path),
    )
    provider = AsyncMock(
        return_value=LLMResponse(
            content="preserved fact", usage={"prompt_tokens": 2, "completion_tokens": 2}
        )
    )
    client._chat_with_retries = provider
    executor = OperationExecutor(
        OperationRegistry(make_model_operations(client, AsyncMock(return_value="system"))),
        CallContext("a", "root", "model"),
        store,
    )
    adapter = DSLAdapter(executor)
    builtins = {**program_builtins(), **adapter.builtins()}
    # Token estimation is deterministic; the real model-context operation still
    # records and replays its result through the same invocation authority.
    monkeypatch.setattr("tlon.base.model_limits.litellm.token_counter", lambda **kwargs: 80)
    store.freeze_model_context(
        "root",
        "config",
        {"model_name": "openai/test", "context_window": 100, "max_output_tokens": 16},
    )
    sources = {
        "work.tlon": """(ns work (:require [tlon.context :as context]) (:export handle))
      (defn handle [event]
        (context/handoff {:messages [{:role "user" :content "old fact"}
                                    {:role "user" :content "recent"}]
                          :max_tokens 16} "root"))"""
    }
    package = ModulePackage.from_sources(program_module_sources(sources), builtins)
    commit = executor._commit

    async def interrupt(
        invocation: Invocation, outcome: InvocationOutcome, inputs: dict[str, Any]
    ) -> None:
        if invocation.name == "model-call":
            raise asyncio.CancelledError()
        await commit(invocation, outcome, inputs)

    executor._commit = interrupt
    first = ProgramDriver(
        store, "root", builtins, operations=adapter, member_id="a", token_budget=64
    )
    with pytest.raises(asyncio.CancelledError):
        await first.run_entry(package.entry("work/handle"), {}, fuel=10000)
    used = store.budget_usage("root")
    executor._commit = commit
    resumed = ProgramDriver(
        store, "root", builtins, operations=adapter, member_id="a", token_budget=64
    )
    result = await resumed.run_entry(package.entry("work/handle"), {}, fuel=10000)
    messages = result[Keyword("messages")]
    assert [message[Keyword("content")] for message in messages[:2]] == ["old fact", "recent"]
    assert messages[-1][Keyword("content")] == "preserved fact"
    continuation = result[Keyword("continuation")]
    assert continuation[Keyword("source_run_id")] == "root"
    assert "preserved fact" in continuation[Keyword("messages")][0][Keyword("content")]
    assert store.budget_usage("root") == used
    assert used["used"] == 2
    provider.assert_awaited_once()
