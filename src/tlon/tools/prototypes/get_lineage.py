"""Prototype lineage query tool — the genealogy as first-class data ()."""

from collections.abc import Callable
from typing import Any

from tlon.tools.base import Tool, ToolResult
from tlon.tools.prototypes.ports import PrototypeError


class GetPrototypeLineageTool(Tool):
    """Tool to walk a prototype's ``:parent`` chain up to the root.

    The lineage is derived, never stored: the injected ``lineage`` callable
    (``agents.prototypes.get_prototype_lineage``, : tools must not
    import ``tlon.agents``) reads each generation's own metadata and diffs
    adjacent ``behavior.tlon`` texts. A deleted/renamed parent marks the
    oldest reachable generation ``broken``.
    """

    def __init__(self, lineage: Callable[[str], list[dict[str, Any]]]) -> None:
        super().__init__(
            name="getPrototypeLineage",
            description=(
                "查询一个 prototype 的谱系：沿 :parent 链向上走到根原型，返回每一代的"
                "名称、代数（generation）、作者、变更说明（proposal_note）、trusted 标志，"
                "以及相邻两代 behavior.tlon 的 unified diff。"
                "父原型已被删除/改名时链在断点处截断并标注 broken。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "prototype 名称（trusted 库或 staging 区皆可）",
                    },
                },
                "required": ["name"],
            },
        )
        self._lineage = lineage

    async def execute(self, **kwargs: Any) -> ToolResult:
        name: str = kwargs["name"]
        try:
            chain = self._lineage(name)
        except PrototypeError as e:
            return ToolResult(success=False, data=None, error=str(e), message=f"谱系查询失败: {e}")
        return ToolResult(
            success=True,
            data={"lineage": chain},
            message=f"prototype '{name}' 的谱系共 {len(chain)} 代",
        )
