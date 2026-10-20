"""Self scheduling operations declare transitions without touching the registry."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from tlon.lifecycle.ports import SelfStateReader
from tlon.lifecycle.service import LifecycleService
from tlon.operations.adapters import OperationTool
from tlon.operations.core import CallContext, OperationExecutor, OperationRegistry
from tlon.operations.lifecycle import make_lifecycle_operations


def _tool(name: str, registry: SelfStateReader | None = None) -> OperationTool:
    service = LifecycleService("self", state_reader=registry)
    executor = OperationExecutor(
        OperationRegistry(make_lifecycle_operations(service)), CallContext("self", "test", "test")
    )
    return OperationTool(executor, name)


@pytest.mark.parametrize("name", ["finish-turn", "sleep-until", "exit-agent"])
async def test_other_members_cannot_be_addressed(name: str) -> None:
    result = await _tool(name).execute_context({"agent_id": "someone-else"})
    assert not result.success


async def test_finish_only_declares_scheduling() -> None:
    result = await _tool("finish-turn").execute_context({})
    assert result.success
    assert result.data == {"scheduling_request": {"action": "finish", "reason": ""}}


async def test_exit_declares_permanent_self_exit() -> None:
    result = await _tool("exit-agent").execute_context({"reason": "完成使命"})
    assert result.success
    assert result.data == {"scheduling_request": {"action": "exit", "reason": "完成使命"}}


@pytest.mark.parametrize("value", ["tomorrow", "2040-01-01T00:00:00", "2000-01-01T00:00:00Z"])
async def test_invalid_sleep_does_not_declare_transition(value: str) -> None:
    result = await _tool("sleep-until").execute_context({"wake_at": value})
    assert not result.success
    assert not result.data


async def test_sleep_normalizes_offset_to_utc() -> None:
    future = datetime.now(UTC) + timedelta(hours=2)
    result = await _tool("sleep-until").execute_context(
        {"wake_at": future.isoformat(), "reason": "休息"}
    )
    assert result.success
    request = result.data["scheduling_request"]
    assert request["action"] == "sleep"
    assert datetime.fromisoformat(request["wake_at"]) == future
    assert request["wake_at"].endswith("+00:00")


async def test_get_state_reads_only_self() -> None:
    registry = MagicMock()
    registry.get_agent_status = AsyncMock(
        return_value={"id": "self", "state": "active", "wake_at": None}
    )
    tool = _tool("member-self-state", registry)
    assert not (await tool.execute_context({"agent_id": "other"})).success
    result = await tool.execute_context({})
    assert result.data == registry.get_agent_status.return_value
    registry.get_agent_status.assert_awaited_once_with("self")
