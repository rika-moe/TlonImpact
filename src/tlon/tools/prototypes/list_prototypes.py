"""List behavior prototypes tool."""

from tlon.agents.prototypes import list_prototypes
from tlon.tools.base import Tool, ToolResult


class ListPrototypesTool(Tool):
    """Tool to list available behavior prototypes.

    Reads ``~/.tlon/prototypes/`` (the seeded, user-editable library),
    so prototypes added or modified by the user are visible to agents.
    """

    def __init__(self) -> None:
        super().__init__(
            name="listPrototypes",
            description=(
                "列出可用的 behavior prototype（行为原型）。每个 prototype 是一套预制的"
                "行为规约 + 人格模板，可作为创建新 Agent 的模板。返回名称、显示名与描述。"
            ),
            parameters={
                "type": "object",
                "properties": {},
                "required": [],
            },
        )

    async def execute(self) -> ToolResult:
        prototypes = list_prototypes()
        data = [
            {
                "name": proto.name,
                "display_name": proto.display_name,
                "description": proto.description,
                "trusted": proto.trusted,
            }
            for proto in prototypes
        ]
        if not data:
            return ToolResult(success=True, data=[], message="暂无可用的 behavior prototype")
        return ToolResult(
            success=True,
            data=data,
            message=f"共 {len(data)} 个可用 prototype",
        )
