"""Real memory persistence, owner isolation and shared entry point checks."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from tests.history_helpers import append_history
from tlon.agents.run_journal import RunJournal
from tlon.base.paths import TlonPaths
from tlon.memory.manager import MemoryManager
from tlon.memory.models import MemoryConfig
from tlon.operations.adapters import OperationTool
from tlon.operations.core import CallContext, OperationExecutor, OperationRegistry
from tlon.operations.dsl import DSLAdapter
from tlon.operations.memory import make_memory_operations, memory_operation_definitions
from tlon.operations.schema import InputError, OperationError, UnknownOutcomeError
from tlosure.builtins import BuiltinCall
from tlosure.edn import Keyword


def services(
    tmp_path: Path, member: str
) -> tuple[MemoryManager, RunJournal, OperationExecutor]:
    memory = MemoryManager(member, MemoryConfig(), tmp_path / member)
    journal = RunJournal(member, TlonPaths(home=tmp_path))
    executor = OperationExecutor(
        OperationRegistry(make_memory_operations(memory, journal)),
        CallContext(member, "run", "call"),
    )
    return memory, journal, executor


async def test_real_memory_both_entries_and_persistence(tmp_path: Path) -> None:
    memory, _, executor = services(tmp_path, "a")
    result = await OperationTool(executor, "memory-remember").execute_context(
        {"fact": "Python preferred", "importance": 0.8}
    )
    assert result.success and result.data["claim_id"]
    adapter = DSLAdapter(executor)
    builtin = adapter.builtins()["memory-search"]
    call = BuiltinCall("memory-search", builtin, [{Keyword("query"): "Python"}], {})
    found = await adapter.handle(call)
    assert "Python preferred" in found[Keyword("results")]
    restored = MemoryManager("a", MemoryConfig(), tmp_path / "a")
    assert "Python preferred" in await restored.search_memories("Python")
    preview = await executor.call("memory-forget", {"query": "Python"})
    assert "确认删除" in preview["result"]
    assert len(await memory.claims.get_all_claims()) == 1
    deleted = await executor.call("memory-forget", {"query": "Python", "confirm": True})
    assert "已删除 1" in deleted["result"]
    assert await memory.claims.get_all_claims() == []


async def test_two_members_memory_and_journal_isolation(tmp_path: Path) -> None:
    _, ja, a = services(tmp_path, "a")
    _, jb, b = services(tmp_path, "b")
    await a.call("memory-remember", {"fact": "private secret"})
    assert "private secret" not in (await b.call("memory-search", {"query": "private"}))["results"]
    for journal, text in ((ja, "A secret"), (jb, "B private")):
        await append_history(journal, "same-id", "run/start", trigger_content=text)
    assert "A secret" in (await a.call("run-read", {"run_id": "same-id"}))["digest"]
    assert "B private" in (await b.call("run-read", {"run_id": "same-id"}))["digest"]
    with pytest.raises(InputError):
        await a.call("memory-search", {"query": "x", "member_id": "b"})
    # A mislabeled executor cannot grant access to B's bound memory service.
    wrong = OperationExecutor(a.registry, CallContext("b", "run", "call"))
    with pytest.raises(OperationError, match="another member"):
        await wrong.call("memory-search", {"query": "secret"})


async def test_unknown_and_cancellation_are_distinct(tmp_path: Path) -> None:
    memory, _, executor = services(tmp_path, "a")
    memory.remember_fact = AsyncMock(side_effect=OSError("write uncertain"))
    with pytest.raises(UnknownOutcomeError) as failure:
        await executor.call("memory-remember", {"fact": "x"})
    assert failure.value.data == {"outcome": "unknown", "call_id": "call", "retry_safe": False}
    memory.remember_fact = AsyncMock(side_effect=asyncio.CancelledError)
    with pytest.raises(asyncio.CancelledError):
        await executor.call("memory-remember", {"fact": "x"})


def test_static_contracts() -> None:
    assert len(memory_operation_definitions()) == 4
    assert all(
        op.input_schema["additionalProperties"] is False for op in memory_operation_definitions()
    )
