"""Self scheduling requests are separate from mail and bound to the caller."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from tlon.tools.agents.scheduling import (
    ExitAgentTool,
    FinishTurnTool,
    GetSelfStateTool,
    SleepUntilTool,
)


@pytest.mark.parametrize("tool", [FinishTurnTool(), SleepUntilTool(), ExitAgentTool()])
def test_other_members_cannot_be_addressed(
    tool: FinishTurnTool | SleepUntilTool | ExitAgentTool,
) -> None:
    assert tool.validate_arguments({"agent_id": "someone-else"})


async def test_finish_only_declares_scheduling() -> None:
    result = await FinishTurnTool().execute()
    assert result.success
    assert result.data == {"scheduling_request": {"action": "finish", "reason": ""}}


async def test_exit_declares_permanent_self_exit() -> None:
    result = await ExitAgentTool().execute(reason="完成使命")
    assert result.success
    assert result.data == {"scheduling_request": {"action": "exit", "reason": "完成使命"}}


@pytest.mark.parametrize("value", ["tomorrow", "2040-01-01T00:00:00", "2000-01-01T00:00:00Z"])
async def test_invalid_sleep_does_not_declare_transition(value: str) -> None:
    result = await SleepUntilTool().execute(wake_at=value)
    assert not result.success
    assert "scheduling_request" not in result.data


async def test_sleep_normalizes_offset_to_utc() -> None:
    future = datetime.now(UTC) + timedelta(hours=2)
    result = await SleepUntilTool().execute(wake_at=future.isoformat(), reason="休息")
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
    tool = GetSelfStateTool(registry, "self")
    assert tool.validate_arguments({"agent_id": "other"})
    result = await tool.execute()
    assert result.data == registry.get_agent_status.return_value
    registry.get_agent_status.assert_awaited_once_with("self")
