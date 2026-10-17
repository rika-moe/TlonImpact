"""List all agents as an addressing yellow page (ADR 0024 决策 3).

The member-facing roster carries exactly three fields per entry — id /
name / mail address — and nothing else: the yellow page answers only
「给谁写信能到」. Identity content (prototype, lineage) and runtime state
are deliberately projected away inside the tool; the registry's full
status dict keeps serving the human management surface (REST).
"""

from typing import Any

from tlon.organization.mail.mail import address_for_member
from tlon.tools.agents.ports import AgentLifecycle
from tlon.tools.base import Tool, ToolResult


class ListAgentsTool(Tool):
    """Tool to list every registered agent as an addressing yellow page.

    Each entry is ``{"id", "name", "address"}`` — enough to confirm a
    spawn/stop target exists and to address a letter. It says nothing
    about who a member *is*: social identity lives in each member's own
    contact book (subjective, ADR 0024), not in any system roster.
    """

    def __init__(self, registry: AgentLifecycle) -> None:
        super().__init__(
            name="listAgents",
            description=(
                "列出全部 Agent 的寻址黄页：id、显示名、邮件地址（id@tlon.internal）。"
                "给成员写信前用它查地址，spawn 或 stop 前用它确认目标 id 存在。\n"
                "注意：黄页只回答「给谁写信能到」，不代表任何人的社会身份——"
                "你对成员的认识以你自己的联系簿与通信为准。"
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
            {
                "id": status["id"],
                "name": status["name"],
                "address": address_for_member(status["id"]),
            }
            for status in await self._registry.get_all_agent_status()
            if status is not None and status.get("state") != "retired"
        ]
        agents.sort(key=lambda entry: entry["id"])
        return ToolResult(
            success=True,
            data=agents,
            message=f"共 {len(agents)} 个 agent",
        )
