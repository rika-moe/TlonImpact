"""List staging prototypes tool — proposals awaiting human approval ()."""

from collections.abc import Callable, Sequence
from typing import Any

from tlon.tools.base import Tool, ToolResult
from tlon.tools.prototypes.ports import PrototypeSummary


class ListStagingPrototypesTool(Tool):
    """Tool to list prototypes awaiting human trust approval.

    Reads ``~/.tlon/prototypes-staging/`` — the untrusted staging area
    where LLM-authored prototypes land. The actual listing is injected
    as ``lister`` (: tools must not import ``tlon.agents``).
    """

    def __init__(self, lister: Callable[[], Sequence[PrototypeSummary]]) -> None:
        super().__init__(
            name="listStagingPrototypes",
            description=(
                "列出原型暂存区（staging）里等待人类批准的 prototype 提案（只读）。"
                "这些是 agent 写好但尚未获人类信任批准（:trusted false）的原型提案，"
                "需要人类决策：批准后才进入可信原型库、可被 spawn。"
                "返回名称、显示名、描述与作者（author）。"
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
                "author": proto.author,
            }
            for proto in prototypes
        ]
        if not data:
            return ToolResult(success=True, data=[], message="暂存区暂无待批准的 prototype 提案")
        return ToolResult(
            success=True,
            data=data,
            message=f"共 {len(data)} 个待人类批准的 prototype 提案",
        )
