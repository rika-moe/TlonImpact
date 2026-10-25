"""Drafting is an ordinary, bounded tool conversation with repair feedback."""

from pathlib import Path
from typing import Any

import pytest

from tlon.agents.program_package import program_builtins, program_module_sources
from tlosure.builtins import Builtin, EvalError
from tlosure.edn import Keyword, loads
from tlosure.eval import eval_form
from tlosure.modules import ModulePackage


def test_draft_reads_checks_repairs_and_returns_candidate() -> None:
    requests: list[Any] = []
    names = ["file-read", "program-check", "program-check"]
    candidates = [
        loads('{:path "tlon/docs/README.md"}'),
        loads('{:behavior "invalid" :modules {"work.tlon" "bad"}}'),
        loads('{:behavior "valid" :modules {"work.tlon" "good"}}'),
    ]

    def model(_env: Any, request: Any) -> Any:
        index = len(requests)
        requests.append(request)
        return {
            Keyword("tool_calls"): [
                {
                    Keyword("id"): str(index),
                    Keyword("name"): names[index],
                    Keyword("arguments"): candidates[index],
                }
            ]
        }

    def execute(_env: Any, request: Any) -> Any:
        call = request[Keyword("tool_calls")][0]
        index = int(call[Keyword("id")])
        return [
            {
                Keyword("id"): str(index),
                Keyword("name"): names[index],
                Keyword("success"): True,
                Keyword("data"): (
                    {Keyword("valid"): index == 2, Keyword("diagnostics"): ["repair"]}
                    if index
                    else "author documentation"
                ),
                Keyword("error"): None,
            }
        ]

    registry = program_builtins()
    registry.update(
        {
            "model-call": Builtin(model, pure=False),
            "model-context": Builtin(lambda env, args: loads("{:fits true}"), pure=False),
            "tool-execute": Builtin(execute, pure=False),
        }
    )
    package = ModulePackage.from_sources(
        program_module_sources(
            {
                "work.tlon": "(ns work (:require [tlon.authoring :as authoring]) (:export handle)) "
                "(defn handle [options] (authoring/draft options))",
            }
        ),
        registry,
    )
    result = eval_form(
        loads(
            '(handle {:messages [{:role "user" :content "Write a reply"}] '
            ":max_rounds 3 :max_tokens 256})"
        ),
        {"handle": package.entry("work/handle")},
        registry,
        fuel=10000,
    )
    assert result == candidates[2]
    assert len(requests) == 3
    assert "author documentation" in str(requests[1])
    assert "repair" in str(requests[2])
    assert all("prototype-write" not in str(request[Keyword("tools")]) for request in requests)


def test_draft_rejects_unbounded_options_without_model_call() -> None:
    registry = program_builtins()
    package = ModulePackage.from_sources(
        program_module_sources(
            {
                "work.tlon": "(ns work (:require [tlon.authoring :as authoring]) (:export handle)) "
                "(defn handle [options] (authoring/draft options))",
            }
        ),
        registry,
    )
    with pytest.raises(EvalError, match="max_rounds"):
        eval_form(
            loads("(handle {:messages [] :max_rounds 0 :max_tokens 64})"),
            {"handle": package.entry("work/handle")},
            registry,
            fuel=10000,
        )


async def test_draft_recovers_model_receipt_and_keeps_repair_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio
    from unittest.mock import AsyncMock

    from tlon.agents.program_driver import ProgramDriver
    from tlon.agents.program_package import check_program_sources
    from tlon.agents.program_store import ProgramStore
    from tlon.base.config import LLMConfig
    from tlon.base.llm_client import LLMClient, LLMResponse, ToolCall
    from tlon.base.paths import TlonPaths
    from tlon.operations.adapters import OperationTool
    from tlon.operations.core import CallContext, OperationExecutor, OperationRegistry
    from tlon.operations.dsl import DSLAdapter
    from tlon.operations.invocation import Invocation, InvocationOutcome
    from tlon.operations.model import make_model_operations
    from tlon.operations.program import program_check_operation
    from tlon.operations.schema import json_value
    from tlon.operations.tool_dispatch import ToolDispatch

    store = ProgramStore(tmp_path / "authoring.sqlite", build_id="test")
    client = LLMClient(
        LLMConfig(model_name="openai/test", max_output_tokens=8192, context_window=128000),
        agent_id="a",
        tlon_paths=TlonPaths(home=tmp_path),
    )
    behavior = "{:version 2 :handlers {:mail-delivered work/handle} :fuel 1000}"
    candidate = {
        "behavior": behavior,
        "modules": {
            "work.tlon": "(ns work (:export handle)) (defn handle [event] {:result event})"
        },
    }
    invalid = {
        **candidate,
        "modules": {
            "work.tlon": "(ns work (:export handle)) (defn handle [event] (missing event))"
        },
    }
    provider = AsyncMock(
        side_effect=[
            LLMResponse(content=None, tool_calls=[ToolCall("bad", "program-check", invalid)]),
            LLMResponse(content=None, tool_calls=[ToolCall("fixed", "program-check", candidate)]),
        ]
    )
    client._chat_with_retries = provider
    checked: list[dict[str, Any]] = []

    def check(behavior: str, modules: dict[str, str]) -> dict[str, Any]:
        checked.append({"behavior": behavior, "modules": modules})
        return check_program_sources(behavior, modules)

    catalog: list[dict[str, Any]] = []
    executor = OperationExecutor(
        OperationRegistry(
            (
                *make_model_operations(client, AsyncMock(return_value="system"), lambda: catalog),
                program_check_operation(check),
            )
        ),
        CallContext("a", "root", "authoring"),
        store,
    )
    catalog.append(OperationTool(executor, "program-check").to_openai_definition())
    adapter = DSLAdapter(executor, ToolDispatch(executor, lambda: catalog))
    builtins = {**program_builtins(), **adapter.builtins()}
    monkeypatch.setattr("tlon.base.model_limits.litellm.token_counter", lambda **kwargs: 2)
    store.freeze_model_context(
        "root",
        "config",
        {"model_name": "openai/test", "context_window": 4096, "max_output_tokens": 16},
    )
    package = ModulePackage.from_sources(
        program_module_sources(
            {
                "author.tlon": """
      (ns author (:require [tlon.authoring :as authoring]) (:export handle))
      (defn handle [event]
        (authoring/draft {:messages [{:role "user" :content "write"}]
                         :tools ["program-check"] :max_tokens 16 :max_rounds 2}))
    """
            }
        ),
        builtins,
    )
    commit = executor._commit

    async def interrupted(
        invocation: Invocation, outcome: InvocationOutcome, inputs: dict[str, Any]
    ) -> None:
        if invocation.name == "model-call":
            raise asyncio.CancelledError()
        await commit(invocation, outcome, inputs)

    executor._commit = interrupted
    first = ProgramDriver(
        store, "root", builtins, operations=adapter, member_id="a", token_budget=32
    )
    with pytest.raises(asyncio.CancelledError):
        await first.run_entry(package.entry("author/handle"), {}, fuel=10000)
    assert provider.await_count == 1
    assert checked == []
    assert store.budget_usage("root")["used"] == 16
    executor._commit = commit
    resumed = ProgramDriver(
        store, "root", builtins, operations=adapter, member_id="a", token_budget=32
    )
    result = await resumed.run_entry(package.entry("author/handle"), {}, fuel=10000)
    assert json_value(result, edn_keys=True) == candidate
    assert provider.await_count == 2
    assert checked == [invalid, candidate]
    assert store.budget_usage("root")["used"] == 32
    replay = ProgramDriver(
        store, "root", builtins, operations=adapter, member_id="a", token_budget=32
    )
    assert await replay.run_entry(package.entry("author/handle"), {}, fuel=10000) == result
    assert provider.await_count == 2
    assert checked == [invalid, candidate]
    assert store.budget_usage("root")["used"] == 32
