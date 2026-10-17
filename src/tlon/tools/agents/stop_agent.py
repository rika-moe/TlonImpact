"""Retire a member after draining, preserving history and refusing future mail."""

from typing import Any

from tlon.tools.agents.ports import AgentLifecycle, StopRejectedError
from tlon.tools.base import Tool, ToolResult


class StopAgentTool(Tool):
    """Tool to stop a member.

    ADR 0030: explicit stop permanently retires the member, preserving
    persona, memory and mail history. Future mail and wake are refused;
    idle scale-to-zero remains reversible sleep. Drain semantics: a member with live
    (ACTIVE/WAITING) sessions is never stopped — with ``when_idle=true``
    the stop is not rejected but registered as pending and fires the
    moment the sessions drain.
    """

    def __init__(self, registry: AgentLifecycle, spawner_id: str) -> None:
        super().__init__(
            name="stopAgent",
            description=(
                "退休一个 Agent（保留历史数据）：关闭后拒收新信且不再唤醒，"
                "有未完成会话时会被拒绝（先等它完工），或传 when_idle=true 登记"
                "「空闲后停止」——会话全部完成后自动执行，无需反复重试。"
                "数据（人格、记忆、邮箱）永久保留。空闲自动休眠不属于退休。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "agent_id": {
                        "type": "string",
                        "description": "要停止的 agent_id",
                    },
                    "when_idle": {
                        "type": "boolean",
                        "description": (
                            "true 时若有未完成会话则不拒绝，登记挂起停止，"
                            "会话清空后自动执行；默认 false（照旧拒绝）"
                        ),
                    },
                },
                "required": ["agent_id"],
            },
        )
        self._registry = registry
        self._spawner_id = spawner_id

    async def execute(self, **kwargs: Any) -> ToolResult:
        agent_id: str = kwargs["agent_id"]
        when_idle = bool(kwargs.get("when_idle", False))

        try:
            stopped = await self._registry.stop_member(
                agent_id=agent_id, stopped_by=self._spawner_id, when_idle=when_idle
            )
        except StopRejectedError as e:
            return ToolResult(
                success=False,
                data={"reason": e.reason},
                error=f"stop_rejected: {e.reason}",
                message=str(e),
            )
        if not stopped:
            return ToolResult(
                success=True,
                data={"agent_id": agent_id, "pending": True},
                message=(
                    f"成员 {agent_id} 有未完成会话：已登记挂起停止，"
                    "会话全部完成后自动执行，无需再调用"
                ),
            )
        return ToolResult(
            success=True,
            data={"agent_id": agent_id, "stopped": True},
            message=f"成员 {agent_id} 已退休（历史保留，拒收新信）",
        )
