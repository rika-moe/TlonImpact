"""Write an LLM-authored behavior prototype to the staging area (stage 4a)."""

from typing import Any

from tlon.agents.prototypes import PrototypeError, write_staging_prototype
from tlon.base.logger import get_logger
from tlon.tools.base import Tool, ToolResult

logger = get_logger(__name__)

_BEHAVIOR_HINT = (
    "behavior 必须是 v1 schema 的 EDN map：:version 1 必填；可选 :trigger "
    "{:on-mention bool}、:context {:persona :protocol :knowledge "
    "bool :mail-history int}、:loop {:mode :react :max-iterations int :tools "
    ":background :max-consecutive-errors int}、:on-complete {:save-interaction "
    "bool :reflect {:enabled bool :interval-minutes int}}"
)


class WritePrototypeTool(Tool):
    """Tool to author a new behavior prototype (pure-data spec + persona).

    The prototype lands in ``~/.tlon/prototypes-staging/`` marked untrusted:
    it cannot be spawned until a human approves it in the mirror (the
    Gatekeeper model of stage 4a). Validation errors come back verbatim so
    the LLM can fix its EDN and retry.
    """

    def __init__(self, author_id: str) -> None:
        super().__init__(
            name="writePrototype",
            description=(
                "创作一个新的 behavior prototype（行为规约 + 人格模板），写入 staging 区。"
                "新 prototype 默认不可信：需人类在 mirror 中批准后才能被 spawnAgent 使用。"
                "behavior 是 v1 schema 的纯数据 EDN（只描述行为，无法授予工具）。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "prototype 名称（字母/数字/'-'/'_'，全局唯一）",
                    },
                    "display_name": {
                        "type": "string",
                        "description": "显示名（人类在 mirror 审批时看到）",
                    },
                    "description": {
                        "type": "string",
                        "description": "一句话描述这个原型的行为定位",
                    },
                    "behavior": {
                        "type": "string",
                        "description": "行为规约（EDN 字符串）。",
                    },
                    "identity": {
                        "type": "string",
                        "description": "可选 IDENTITY.md 内容（角色定位与自我认知）",
                    },
                    "soul": {
                        "type": "string",
                        "description": "可选 SOUL.md 内容（职责、性格，其他同事可见）",
                    },
                    "rules": {
                        "type": "string",
                        "description": "可选 RULES.md 内容（工作流程与禁止事项）",
                    },
                },
                "required": ["name", "behavior"],
            },
        )
        self._author_id = author_id

    async def execute(self, **kwargs: Any) -> ToolResult:
        name: str = kwargs["name"]
        behavior: str = kwargs["behavior"]
        display_name: str = kwargs.get("display_name", "")
        description: str = kwargs.get("description", "")
        identity: str = kwargs.get("identity", "")
        soul: str = kwargs.get("soul", "")
        rules: str = kwargs.get("rules", "")

        persona = {"IDENTITY.md": identity, "SOUL.md": soul, "RULES.md": rules}
        try:
            info = write_staging_prototype(
                name,
                behavior,
                persona=persona,
                display_name=display_name,
                description=description,
                author_id=self._author_id,
            )
        except PrototypeError as e:
            return ToolResult(
                success=False,
                data={"hint": _BEHAVIOR_HINT},
                error=str(e),
                message=f"prototype 校验失败，未写入: {e}",
            )

        data: dict[str, Any] = {
            "name": info.name,
            "display_name": info.display_name,
            "trusted": info.trusted,
            "status": "pending_approval",
        }
        return ToolResult(
            success=True,
            data=data,
            message=(
                f"prototype '{info.name}' 已写入 staging 区（不可信）。"
                "需人类在 mirror 的「规约审批」中批准后才能 spawn。"
            ),
        )
