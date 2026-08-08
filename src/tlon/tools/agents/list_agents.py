"""List all agents with their runtime state and identity."""

from typing import TYPE_CHECKING, Any

from tlon.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from tlon.agents.agent_registry import AgentRegistry


class ListAgentsTool(Tool):
    """Tool to list every registered agent.

    Covers the persona full set (running, sleeping, waking), each with its
    identity fields: role (resident/contractor), source prototype, and
    spawner. Useful before spawnAgent/stopAgent to see who exists.
    """

    def __init__(self, registry: "AgentRegistry") -> None:
        super().__init__(
            name="listAgents",
            description=(
                "列出全部 Agent：运行状态（running/sleeping/waking）、身份"
                "（resident 居民 / contractor 临时工）、来源 prototype、"
                "由谁 spawn。spawn 或 stop 前可用它确认目标。"
            ),
            parameters={
                "type": "object",
                "properties": {},
                "required": [],
            },
        )
        self._registry = registry

    async def execute(self, **kwargs: Any) -> ToolResult:
        agents = [
            status
            for status in self._registry.get_all_agent_status()
            if status is not None
        ]
        return ToolResult(
            success=True,
            data=agents,
            message=f"共 {len(agents)} 个 agent",
        )
