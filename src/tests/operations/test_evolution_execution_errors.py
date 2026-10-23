"""Real evolution model calls preserve known rejection and unknown outcomes."""

from functools import partial
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from litellm import BadRequestError

from tlon.agents.program_store import ProgramStore
from tlon.agents.prototypes.catalog import get_prototype_detail
from tlon.agents.prototypes.staging import append_staging_review, write_staging_prototype
from tlon.agents.session_journal import SessionJournal
from tlon.base.config import LLMConfig
from tlon.base.execution import (
    BudgetExceededError,
    ExecutionDeadlineError,
    ExecutionScope,
    bind_execution,
)
from tlon.base.llm_client import LLMClient
from tlon.base.paths import TlonPaths
from tlon.evolution.propose_behavior import BehaviorProposer
from tlon.evolution.review_prototype import PrototypeReviewer
from tlon.memory.manager import MemoryManager
from tlon.memory.models import MemoryConfig
from tlon.operations.adapters import OperationTool
from tlon.operations.core import CallContext, OperationExecutor, OperationRegistry
from tlon.operations.dsl import DSLAdapter
from tlon.operations.evolution import make_evolution_operations
from tlon.operations.schema import RejectedError, UnknownOutcomeError, edn_value
from tlosure.builtins import BuiltinCall

_SPEC = '{:version 1 :rules [{:name "boot" :on :boot :actions []}]}'


def staging_snapshot(paths: TlonPaths) -> dict[str, bytes]:
    return {
        str(p.relative_to(paths.tlon_home())): p.read_bytes()
        for root in (paths.prototypes_staging_dir(),)
        if root.exists()
        for p in root.rglob("*")
        if p.is_file()
    }


def executor(
    paths: TlonPaths, store: ProgramStore, client: LLMClient
) -> tuple[OperationExecutor, dict[str, dict[str, str]]]:
    behavior = paths.agent_behavior_file("alice")
    behavior.parent.mkdir(parents=True, exist_ok=True)
    behavior.write_text(_SPEC)
    memory = MemoryManager("alice", MemoryConfig(), paths.agent_dir("alice"), tlon_paths=paths)
    logs = SessionJournal("alice", paths)
    info = write_staging_prototype(_SPEC, display_name="existing", tlon_paths=paths)
    services = make_evolution_operations(
        "alice",
        True,
        behavior_proposer=BehaviorProposer(
            "alice",
            None,
            "alice",
            client,
            memory,
            logs,
            partial(write_staging_prototype, tlon_paths=paths),
            paths,
        ),
        reviewer=PrototypeReviewer(
            "alice",
            client,
            partial(get_prototype_detail, staging=True, tlon_paths=paths),
            lambda _name: [],
            partial(append_staging_review, tlon_paths=paths),
        ),
    )
    return OperationExecutor(
        OperationRegistry(services), CallContext("alice", "root", "call"), store
    ), {
        "behavior-propose": {"intent": "improve", "display_name": "next"},
        "prototype-review": {"name": info.name},
    }


@pytest.mark.parametrize("name", ["behavior-propose", "prototype-review"])
@pytest.mark.parametrize("fault", ["budget", "rejected", "context-rejected", "unknown"])
async def test_real_evolution_model_failure_is_identical_in_dsl_tool_and_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, fault: str
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    provider = AsyncMock(
        side_effect=BadRequestError(
            "maximum context length exceeded"
            if fault == "context-rejected"
            else "explicit refusal",
            model="gpt-4o",
            llm_provider="openai",
        )
        if fault in {"rejected", "context-rejected"}
        else TimeoutError("provider response lost")
    )
    monkeypatch.setattr("litellm.acompletion", provider)
    paths = TlonPaths(home=tmp_path)
    store = ProgramStore(tmp_path / "calls.sqlite", build_id="test")
    client = LLMClient(LLMConfig(model_name="openai/gpt-4o", max_tokens=10, max_retries=3))
    scope = ExecutionScope(store, "root", 0 if fault == "budget" else 10, "output")
    bound, arguments = executor(paths, store, client)
    adapter = DSLAdapter(bound)
    before = staging_snapshot(paths)
    expected = {
        "budget": BudgetExceededError,
        "rejected": RejectedError,
        "context-rejected": RejectedError,
        "unknown": UnknownOutcomeError,
    }[fault]
    with bind_execution(scope), pytest.raises(expected):
        await adapter.handle(
            BuiltinCall(name, adapter.builtins()[name], [edn_value(arguments[name])], {})
        )
    # Recovery uses a fresh scope and the same frozen root/call, not a new allowance.
    with bind_execution(ExecutionScope(store, "root", scope.token_limit, "output")):
        if fault in {"rejected", "context-rejected"}:
            result = await OperationTool(bound, name).execute_context(arguments[name])
            assert not result.success and expected.__name__ in result.error
            assert not result.data or result.data.get("outcome") != "unknown"
        else:
            with pytest.raises(expected) as failure:
                await OperationTool(bound, name).execute_context(arguments[name])
            if fault == "unknown":
                assert failure.value.data["outcome"] == "unknown"
    assert provider.await_count == (0 if fault == "budget" else 1)
    assert staging_snapshot(paths) == before


@pytest.mark.parametrize(
    "name,slot,args",
    [
        ("behavior-propose", "behavior_proposer", {"intent": "x", "display_name": "x"}),
        ("prototype-review", "reviewer", {"name": "proposal"}),
    ],
)
async def test_evolution_wrapper_preserves_known_deadline(
    name: str, slot: str, args: dict[str, Any]
) -> None:
    service = AsyncMock()
    service.execute.side_effect = ExecutionDeadlineError("known deadline")
    operations = make_evolution_operations("alice", True, **{slot: service})
    bound = OperationExecutor(OperationRegistry(operations), CallContext("alice", "run", "call"))
    with pytest.raises(ExecutionDeadlineError):
        await bound.call(name, args)
