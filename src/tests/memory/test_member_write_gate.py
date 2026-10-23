"""Maintenance and operation writes share the member's complete mutation boundary."""

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.agents.test_reflection import _engine
from tlon.agents.program_store import ProgramStore
from tlon.base.config import LLMConfig
from tlon.base.execution import (
    BudgetExceededError,
    ExecutionDeadlineError,
    ExecutionScope,
    MemberWriteGate,
    UnknownModelOutcomeError,
    bind_execution,
)
from tlon.memory.distiller import Distiller
from tlon.memory.manager import MemoryManager
from tlon.memory.models import MemoryConfig, SessionProjection
from tlon.operations.core import CallContext, Operation, OperationExecutor, OperationRegistry
from tlon.operations.memory import make_memory_operations
from tlon.operations.schema import RecoveryMismatchError


@pytest.mark.parametrize("mutation", ["remember", "forget", "delete", "reflection", "distillation"])
async def test_maintenance_waits_for_operation_writer_but_read_can_pass(
    tmp_path: Path, mutation: str
) -> None:
    engine, manager = _engine(tmp_path)
    await manager.load()
    seed = await manager.remember_fact("seed fact")
    gate = manager.write_gate
    entered, release, attempted = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def held(context: CallContext, arguments: dict[str, Any]) -> str:
        entered.set()
        await release.wait()
        return "done"

    store = ProgramStore(None, build_id="gate-test")
    executor = OperationExecutor(
        OperationRegistry(
            [
                Operation(
                    "held-write",
                    "held",
                    {
                        "type": "object",
                        "properties": {},
                        "required": [],
                        "additionalProperties": False,
                    },
                    {"type": "string"},
                    held,
                    effects=frozenset({"write"}),
                )
            ]
        ),
        CallContext("agent1", "root", "held"),
        store,
        write_gate=gate,
    )
    active = asyncio.create_task(executor.call("held-write", {}))
    await entered.wait()

    async def maintenance() -> Any:
        attempted.set()
        if mutation == "remember":
            return await manager.remember_fact("new fact")
        if mutation == "forget":
            return await manager.forget("seed fact", confirm=True)
        if mutation == "delete":
            return await manager.delete_claim(seed)
        if mutation == "reflection":
            return await engine._apply_operation(
                {"action": "record_claim", "description": "reflected fact"}
            )
        return await manager._distill_sessions(
            [SessionProjection("history", "completed work", llm_responses=["useful result"])]
        )

    waiting = asyncio.create_task(maintenance())
    await attempted.wait()
    try:
        assert not waiting.done()
        assert [claim["text"] for claim in await manager.claims.get_all_claims()] == ["seed fact"]
        assert "seed fact" in await asyncio.wait_for(manager.search_memories("seed fact"), 1)
    finally:
        release.set()
        await active
    await asyncio.wait_for(waiting, 1)
    claims = await manager.claims.get_all_claims()
    if mutation in ("forget", "delete"):
        assert claims == []
    else:
        assert len(claims) > 1


async def test_operation_and_manager_reenter_same_task_and_conflict_merge_stays_inside_gate(
    tmp_path: Path,
) -> None:
    gate = MemberWriteGate()
    manager = MemoryManager("agent1", MemoryConfig(), tmp_path, write_gate=gate)
    await manager.load()
    store = ProgramStore(None, build_id="reentry")
    executor = OperationExecutor(
        OperationRegistry(make_memory_operations(manager, MagicMock())),
        CallContext("agent1", "root", "remember"),
        store,
        write_gate=gate,
    )
    result = await asyncio.wait_for(executor.call("memory-remember", {"fact": "first fact"}), 1)
    assert await manager.claims.get_claim(result["claim_id"]) is not None
    entered, release = asyncio.Event(), asyncio.Event()

    async def conflict(claims: Any) -> None:
        entered.set()
        await release.wait()

    manager._resolve_conflicts = conflict  # type: ignore[method-assign]
    first = asyncio.create_task(manager.remember_fact("second fact"))
    await entered.wait()
    delete = asyncio.create_task(manager.delete_claim(result["claim_id"]))
    await asyncio.sleep(0)
    assert not delete.done()
    assert await manager.claims.get_claim(result["claim_id"]) is not None
    release.set()
    await first
    assert await delete


@pytest.mark.parametrize(
    "error",
    [
        BudgetExceededError("budget"),
        ExecutionDeadlineError("deadline"),
        UnknownModelOutcomeError("unknown"),
        RecoveryMismatchError("identity"),
    ],
)
async def test_distillation_control_failure_never_falls_back_to_memory_commit(
    tmp_path: Path, error: Exception
) -> None:
    manager = MemoryManager("agent1", MemoryConfig(), tmp_path)
    await manager.load()
    distiller = Distiller(LLMConfig(model_name="openai/test"))
    distiller.llm_client.chat = AsyncMock(side_effect=error)
    manager.distiller = distiller
    with pytest.raises(type(error)):
        await manager._distill_sessions(
            [SessionProjection("history", "trigger", llm_responses=["result"])]
        )
    assert await manager.claims.get_all_claims() == []
    assert not manager._consumed and manager._pending_batch is not None


async def test_cancelled_maintenance_waiting_for_gate_cannot_write(tmp_path: Path) -> None:
    gate = MemberWriteGate()
    manager = MemoryManager("agent1", MemoryConfig(), tmp_path, write_gate=gate)
    await manager.load()
    scope = ExecutionScope(ProgramStore(None, build_id="cancel"), "maintenance", 1000, "output")
    async with gate.hold():
        with bind_execution(scope):
            waiting = asyncio.create_task(manager.remember_fact("must not persist"))
        await asyncio.sleep(0)
        scope.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert await manager.claims.get_all_claims() == []
