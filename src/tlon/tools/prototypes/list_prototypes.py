"""List behavior prototypes tool."""

from collections.abc import Callable, Sequence
from typing import Any

from tlon.tools.base import Tool, ToolResult
from tlon.tools.prototypes.ports import PrototypeSummary


class ListPrototypesTool(Tool):
    """Tool to list available behavior prototypes.

    Reads ``~/.tlon/prototypes/`` (the seeded, user-editable library),
    so prototypes added or modified by the user are visible to agents.
    The actual listing is injected as ``lister`` (: tools must not
    import ``tlon.agents``).
    """

    def __init__(self, lister: Callable[[], Sequence[PrototypeSummary]]) -> None:
        super().__init__(
            name="listPrototypes",
            description=(
                "列出可用的 behavior prototype（行为原型）。每个 prototype 是一套预制的"
                "行为规约，可作为创建新成员的行为模板；不携带人格，也不赋予职业或职位。返回系统生成的 ID、行为显示名与描述。"
            ),
            parameters={
                "type": "object",
                "properties": {},
                "required": [],
            },
        )
        self._lister = lister

    async def execute(self, **kwargs: Any) -> ToolResult:
        prototypes = self._lister()
        data = [
            {
                "name": proto.name,
                "display_name": proto.display_name,
                "description": proto.description,
                "trusted": proto.trusted,
                "parent": proto.parent,
                "generation": proto.generation,
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
