"""Lifecycle contracts preserve authority, registry budgets and deferred controls."""

from pathlib import Path
from typing import Literal
from unittest.mock import AsyncMock, MagicMock

import pytest
from structlog.contextvars import bound_contextvars

from tlon.base.config import AgentConfig
from tlon.lifecycle.ports import SpawnRejectedError
from tlon.lifecycle.service import LifecycleService
from tlon.operations.core import CallContext, OperationExecutor, OperationRegistry
from tlon.operations.lifecycle import make_lifecycle_operations
from tlon.operations.schema import InputError, OperationError, RejectedError


def _executor(service: LifecycleService) -> OperationExecutor:
    return OperationExecutor(
        OperationRegistry(make_lifecycle_operations(service)),
        CallContext(service.member_id, "run", "call"),
    )


@pytest.mark.parametrize("operation", ["member-start", "member-stop"])
async def test_member_can_only_request_own_lifecycle(operation: str) -> None:
    publish = AsyncMock()
    executor = _executor(LifecycleService("worker", publish_request=publish))
    with pytest.raises(RejectedError, match="own scheduling"):
        await executor.call(operation, {"agent_id": "someone-else"})
    publish.assert_not_awaited()
    result = await executor.call(operation, {"agent_id": "worker", "reason": "rest"})
    assert result == {
        "action": operation.removeprefix("member-"),
        "agent_id": "worker",
        "reason": "rest",
    }
    publish.assert_awaited_once_with(result["action"], "worker", "rest", executor.context)


async def test_adam_start_all_uses_boot_candidates_in_order() -> None:
    publish = AsyncMock()
    executor = _executor(
        LifecycleService("adam", publish_request=publish, start_all_candidates=lambda: ["a", "b"])
    )
    result = await executor.call("member-start-all", {"reason": "boot"})
    assert [request["agent_id"] for request in result] == ["a", "b"]
    assert publish.await_count == 2
    assert [call.args[1] for call in publish.await_args_list] == ["a", "b"]


async def test_start_all_rejects_non_adam_before_candidate_query() -> None:
    candidates = MagicMock(return_value=["worker"])
    executor = _executor(LifecycleService("worker", start_all_candidates=candidates))
    with pytest.raises(RejectedError):
        await executor.call("member-start-all", {})
    candidates.assert_not_called()


async def test_spawn_binds_owner_session_params_and_preserves_budget_rejection() -> None:
    registry = MagicMock()
    registry.spawn_member = AsyncMock(
        return_value=AgentConfig(id="child", name="Child", prototype="source", spawn_depth=1)
    )
    executor = _executor(LifecycleService("worker", registry=registry))
    arguments = {"prototype": "source", "name": "Child", "params": {"topics": ["a", "b"]}}
    with bound_contextvars(session_id="session-a"):
        result = await executor.call("member-spawn", arguments)
    assert result == {"agent_id": "child", "name": "Child", "prototype": "source", "spawn_depth": 1}
    registry.spawn_member.assert_awaited_once_with(
        prototype="source",
        name="Child",
        spawner_id="worker",
        session_id="run",
        params={"topics": ["a", "b"]},
    )
    registry.spawn_member.side_effect = SpawnRejectedError("session_quota", "quota reached")
    with pytest.raises(RejectedError) as rejection:
        await executor.call("member-spawn", arguments)
    assert rejection.value.data == {"reason": "session_quota"}


async def test_probationary_spawn_rejected_even_if_definition_is_present() -> None:
    registry = MagicMock()
    registry.spawn_member = AsyncMock()
    executor = _executor(LifecycleService("worker", registry=registry, apprentice=True))
    with pytest.raises(RejectedError) as rejection:
        await executor.call("member-spawn", {"prototype": "source", "name": "Child"})
    assert rejection.value.data == {"reason": "probationary"}
    registry.spawn_member.assert_not_awaited()


async def test_caller_cannot_override_bound_owner() -> None:
    executor = _executor(LifecycleService("worker"))
    with pytest.raises(InputError):
        await executor.call(
            "member-spawn", {"prototype": "source", "name": "Child", "spawner_id": "adam"}
        )
    with pytest.raises(OperationError):
        await executor.call("finish-turn", {}, CallContext("adam", "run", "call"))


async def test_finish_is_local_pure_data_without_lifecycle_requests() -> None:
    publish = AsyncMock()
    executor = _executor(LifecycleService("worker", publish_request=publish))
    assert not executor.registry.get("finish-turn").effects
    assert await executor.call("finish-turn", {}) == {"finished": True}
    publish.assert_not_awaited()


async def test_lifecycle_lost_commit_receipt_keeps_unknown_identity() -> None:
    from unittest.mock import AsyncMock

    from tlon.operations.schema import UnknownOutcomeError

    publish = AsyncMock(side_effect=OSError("accepted then response lost"))
    service = LifecycleService("a", publish_request=publish)
    executor = OperationExecutor(
        OperationRegistry(make_lifecycle_operations(service)), CallContext("a", "r", "stable")
    )
    with pytest.raises(UnknownOutcomeError) as failure:
        await executor.call("member-stop", {"agent_id": "a"})
    assert failure.value.data == {
        "outcome": "unknown",
        "call_id": "stable",
        "retry_safe": False,
    }
    publish.assert_awaited_once()


@pytest.mark.parametrize("origin", ["tool", "program", "direct"])
async def test_real_spawn_quota_uses_frozen_root_for_normal_and_recovered_calls(
    tmp_path: Path, origin: Literal["tool", "program", "direct"]
) -> None:
    from types import SimpleNamespace

    from tests.agents.test_spawn import _FakeRegistry, _write_prototype
    from tlon.agents.program_store import ProgramStore
    from tlon.agents.spawn import SpawnManager
    from tlon.base.paths import TlonPaths
    from tlon.operations.adapters import OperationTool
    from tlon.operations.dsl import DSLAdapter
    from tlosure.builtins import BuiltinCall
    from tlosure.edn import Keyword

    paths = TlonPaths(home=tmp_path)
    _write_prototype(paths, "worker")
    manager = SpawnManager(_FakeRegistry(paths), 8, 3, 2)
    store = ProgramStore(tmp_path / "program.sqlite")
    executor = OperationExecutor(
        OperationRegistry(
            make_lifecycle_operations(
                LifecycleService(
                    "agent1", registry=SimpleNamespace(spawn_member=manager.spawn_member)
                )
            )
        ),
        CallContext("agent1", "frozen-root", "unused"),
        store,
    )
    tool = OperationTool(executor, "member-spawn")
    for index in range(2):
        context = CallContext("agent1", "frozen-root", f"spawn:{index}", origin=origin)
        with bound_contextvars(session_id=f"untrusted-log-{index}"):
            result = await tool.execute_context(
                {"prototype": "worker", "name": f"Child {index}"}, context
            )
        assert result.success
    assert manager._quota == {("agent1", "frozen-root"): 2}
    args = {"prototype": "worker", "name": "Must be rejected"}
    if origin == "tool":
        context = CallContext("agent1", "frozen-root", "tool-third", origin="tool")
        result = await tool.execute_context(args, context)
        assert not result.success and "session_quota" in result.error
        # Durable rejection replays with the same identity without consuming quota.
        result = await tool.execute_context(args, context)
        assert not result.success and "session_quota" in result.error
    elif origin == "program":
        adapter = DSLAdapter(executor)
        call = BuiltinCall(
            "member-spawn",
            adapter.builtins()["member-spawn"],
            [{Keyword(key): value for key, value in args.items()}],
            {},
        )
        with pytest.raises(RejectedError, match="session_quota"):
            await adapter.handle(
                call, CallContext("agent1", "frozen-root", "dsl-third", origin="program")
            )
    else:
        with pytest.raises(RejectedError, match="session_quota"):
            await executor.call(
                "member-spawn", args, CallContext("agent1", "frozen-root", "direct-third")
            )
    assert manager._quota == {("agent1", "frozen-root"): 2}
