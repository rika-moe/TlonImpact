"""Source-defined compaction reuses the root's committed model receipt and budget."""

import asyncio
from pathlib import Path
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
from tlon.operations.model import make_model_operations
from tlosure.builtins import Builtin
from tlosure.edn import Keyword
from tlosure.modules import ModulePackage


async def test_compaction_recovers_inner_commit_without_new_budget(tmp_path: Path) -> None:
    store = ProgramStore(tmp_path / "run.sqlite", build_id="test")
    client = LLMClient(
        LLMConfig(model_name="openai/test"), agent_id="a", tlon_paths=TlonPaths(home=tmp_path)
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
    # Pressure estimation itself is covered by host tests; this isolates the model commit window.
    builtins["model-context"] = Builtin(lambda env, request: {Keyword("fits"): True}, pure=False)
    sources = {
        "work.tlon": """(ns work (:require [tlon.context :as context]) (:export handle))
      (defn handle [event]
        (context/compact {:messages [{:role "user" :content "old fact"}
                                    {:role "user" :content "recent"}]
                          :max_tokens 16 :recent_groups 1}))"""
    }
    package = ModulePackage.from_sources(program_module_sources(sources), builtins)
    commit = executor._commit

    async def interrupt(*args: object) -> None:
        raise asyncio.CancelledError()

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
    assert "preserved fact" in result[Keyword("messages")][0][Keyword("content")]
    assert store.budget_usage("root") == used
    assert used["used"] == 16
    provider.assert_awaited_once()
