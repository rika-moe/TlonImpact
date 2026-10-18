"""Self-bound scheduling requests; the runtime commits them after the turn drains."""

from datetime import UTC, datetime
from typing import Any

from tlon.tools.agents.ports import SelfStateReader
from tlon.tools.base import Tool, ToolResult


class FinishTurnTool(Tool):
    """End this turn without sending mail or altering mailbox read state."""

    def __init__(self) -> None:
        super().__init__(
            name="finishTurn",
            description="结束本次工作，进入等待。不会发信或标读；本轮只能单独调用此工具。",
            parameters={"type": "object", "properties": {}, "required": []},
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        return ToolResult(
            success=True, data={"scheduling_request": {"action": "finish", "reason": ""}}
        )


class SleepUntilTool(Tool):
    """Request timed sleep; arriving mail cannot wake the sleeping member."""

    def __init__(self) -> None:
        super().__init__(
            name="sleepUntil",
            description="结束本次工作并睡到未来时刻，期间来信只入箱。wake_at 必须为带时区的 ISO 时间；本轮只能单独调用此工具。",
            parameters={
                "type": "object",
                "properties": {
                    "wake_at": {"type": "string", "description": "未来唤醒时间，ISO 8601 且带时区"},
                    "reason": {"type": "string", "description": "可选，睡眠原因"},
                },
                "required": ["wake_at"],
            },
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        try:
            wake_at = datetime.fromisoformat(kwargs["wake_at"])
            if wake_at.tzinfo is None or wake_at.utcoffset() is None:
                raise ValueError("wake_at 必须带时区")
            wake_at = wake_at.astimezone(UTC)
            if wake_at <= datetime.now(UTC):
                raise ValueError("wake_at 必须是未来时间")
        except (ValueError, TypeError, KeyError) as exc:
            return ToolResult(
                success=False, data={}, error=str(exc), message="睡眠请求无效，继续当前工作。"
            )
        return ToolResult(
            success=True,
            data={
                "scheduling_request": {
                    "action": "sleep",
                    "wake_at": wake_at.isoformat(),
                    "reason": kwargs.get("reason", ""),
                }
            },
        )


class ExitAgentTool(Tool):
    """Request permanent self termination, retaining history and refusing new mail."""

    def __init__(self) -> None:
        super().__init__(
            name="exitAgent",
            description="永久结束自己的生命，拒收新邮件并保留历史；不可恢复。本轮只能单独调用此工具。",
            parameters={
                "type": "object",
                "properties": {
                    "reason": {"type": "string", "description": "可选，退出原因"},
                },
                "required": [],
            },
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        return ToolResult(
            success=True,
            data={
                "scheduling_request": {
                    "action": "exit",
                    "reason": kwargs.get("reason", ""),
                }
            },
        )


class GetSelfStateTool(Tool):
    """Read only the bound member's public scheduling state."""

    def __init__(self, registry: SelfStateReader, agent_id: str) -> None:
        super().__init__(
            name="getSelfState",
            description="查看自己的调度状态和唤醒时间，不改变状态。",
            parameters={"type": "object", "properties": {}, "required": []},
        )
        self._registry = registry
        self._agent_id = agent_id

    async def execute(self, **kwargs: Any) -> ToolResult:
        status = await self._registry.get_agent_status(self._agent_id)
        return ToolResult(
            success=status is not None, data=status or {}, error="" if status else "成员不存在"
        )
