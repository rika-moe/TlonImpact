"""Self scheduling operations declare transitions without touching the registry."""

from unittest.mock import AsyncMock, MagicMock

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


async def test_finish_is_only_a_local_signal() -> None:
    tool = _tool("finish-turn")
    assert not (await tool.execute_context({"agent_id": "other"})).success
    result = await tool.execute_context({})
    assert result.success and result.data == {"finished": True}


def test_scheduling_tools_are_removed() -> None:
    names = {op.name for op in make_lifecycle_operations(None)}
    assert "sleep-until" not in names and "exit-agent" not in names


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
