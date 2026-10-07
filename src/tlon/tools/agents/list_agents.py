"""List all agents with their runtime state and identity."""

from typing import Any

from tlon.tools.agents.ports import AgentLifecycle
from tlon.tools.base import Tool, ToolResult


class ListAgentsTool(Tool):
    """Tool to list every registered agent.

    Covers the persona full set (running, sleeping, waking), each with its
    source prototype (behavioral-scaffold origin — a technical fact, not a
    social identity) and lineage (spawner / depth). Useful before
    spawnAgent/stopAgent to see who exists.
    """

    def __init__(self, registry: AgentLifecycle) -> None:
        super().__init__(
            name="listAgents",
            description=(
                "列出全部 Agent：运行状态（running/sleeping/waking）、来源 "
                "prototype、由谁 spawn。spawn 或 stop 前可用它确认目标。"
                "注意：名册只是寻址黄页，不代表任何人的社会身份——prototype "
                "是认知骨架（行为模板）的技术来源；你对成员的认识以你自己的"
                "联系簿与通信为准。"
            ),
            parameters={
                "type": "object",
                "properties": {},
                "required": [],
            },
        )
        self._registry = registry

    async def execute(self, **kwargs: Any) -> ToolResult:
        agents = [status for status in self._registry.get_all_agent_status() if status is not None]
        return ToolResult(
            success=True,
            data=agents,
            message=f"共 {len(agents)} 个 agent",
        )
