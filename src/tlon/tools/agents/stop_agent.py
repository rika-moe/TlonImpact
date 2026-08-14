"""Stop a contractor agent (drain semantics, residents refused)."""

from typing import Any

from tlon.tools.agents.ports import AgentLifecycle, StopRejectedError
from tlon.tools.base import Tool, ToolResult


class StopAgentTool(Tool):
    """Tool to stop a disposable contractor agent.

    Drain semantics: a contractor with live (ACTIVE/WAITING) sessions is
    never stopped — finish or wait first. Stopping discards the contractor
    entirely: its home directory (persona, memory) is deleted. Residents
    are refused; they can only sleep, never be deleted through this path.
    """

    def __init__(self, registry: AgentLifecycle, spawner_id: str) -> None:
        super().__init__(
            name="stopAgent",
            description=(
                "停止一个临时工 Agent（contractor）：立即关闭并删除其全部数据"
                "（人格、记忆），不可恢复。仅对 contractor 生效；"
                "有未完成会话时会被拒绝（先等它完工）；居民 Agent 不可停止。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "agent_id": {
                        "type": "string",
                        "description": "要停止的 contractor 的 agent_id（c- 开头）",
                    },
                },
                "required": ["agent_id"],
            },
        )
        self._registry = registry
        self._spawner_id = spawner_id

    async def execute(self, **kwargs: Any) -> ToolResult:
        agent_id: str = kwargs["agent_id"]

        try:
            await self._registry.stop_contractor(
                agent_id=agent_id, stopped_by=self._spawner_id
            )
        except StopRejectedError as e:
            return ToolResult(
                success=False,
                data={"reason": e.reason},
                error=f"stop_rejected: {e.reason}",
                message=str(e),
            )
        return ToolResult(
            success=True,
            data={"agent_id": agent_id, "stopped": True},
            message=f"临时工 {agent_id} 已停止并回收",
        )
