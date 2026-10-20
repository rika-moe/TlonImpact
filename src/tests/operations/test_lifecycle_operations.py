"""Lifecycle contracts preserve authority, registry budgets and deferred controls."""

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
        session_id="session-a",
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


@pytest.mark.parametrize(
    "name,arguments,action",
    [
        ("finish-turn", {}, "finish"),
        ("exit-agent", {"reason": "done"}, "exit"),
        ("sleep-until", {"wake_at": "2099-01-01T00:00:00Z"}, "sleep"),
    ],
)
async def test_control_is_metadata_and_declaration_only(
    name: str, arguments: dict, action: str
) -> None:
    registry = MagicMock()
    publish = AsyncMock()
    executor = _executor(LifecycleService("worker", registry=registry, publish_request=publish))
    assert executor.registry.get(name).control
    result = await executor.call(name, arguments)
    assert result["scheduling_request"]["action"] == action
    publish.assert_not_awaited()
    assert not registry.mock_calls


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
