"""Write an LLM-authored behavior prototype to the staging area (stage 4a).

Supports both react (AgentSpec) and script (ScriptSpec) prototypes.
"""

from typing import Any

from tlon.agents.prototypes import PrototypeError, write_staging_prototype
from tlon.base.logger import get_logger
from tlon.tools.base import Tool, ToolResult

logger = get_logger(__name__)

_BEHAVIOR_HINT = (
    "behavior 必须是 v1 schema 的 EDN map：:version 1 必填；:trigger "
    "{:on-mention bool 或 L1 表达式}、:context {:sections [(persona) "
    "(protocol) (knowledge) (mail-history N)]}、:loop {:stages [(think) (act)] "
    ":until (or (iteration-limit-reached?) (error-limit-reached?)) "
    ":max-iterations int :max-consecutive-errors int}、"
    ":on-complete {:save-interaction bool :reflect {:enabled bool "
    ":interval-minutes int}}。可选 :identity / :soul / :rules 为人格文件内容。"
)

_SCRIPT_HINT = (
    "behavior 必须是 v1 ScriptSpec schema 的 EDN map：:version 1 必填；"
    ":rules [{:name str :on event-keyword-or-:boot "
    ":when (纯谓词表达式，可选) :actions [(动作表达式) ...]} ...]。"
    "可选 :parameters 声明模板变量（:type :string 或 :string-list），"
    "引用时用 ${变量名}。script member 不需要 persona 文件。"
)


class WritePrototypeTool(Tool):
    """Tool to author a new behavior prototype (pure-data spec + persona).

    Accepts both react agent specs and script specs (script member /
    deterministic daemon). The prototype lands in
    ``~/.tlon/prototypes-staging/`` marked untrusted: it cannot be spawned
    until a human approves it in the mirror (the Gatekeeper model of
    stage 4a). Validation errors come back verbatim so the LLM can fix
    its EDN and retry.
    """

    def __init__(self, author_id: str) -> None:
        super().__init__(
            name="writePrototype",
            description=(
                "创作一个新的 behavior prototype（行为规约 + 人格模板），写入 staging 区。"
                "支持 spec_type='react'（默认，Agent spec——trigger/context/loop/on-complete）"
                "和 spec_type='script'（script spec——事件规则，零 token 确定性 daemon）。"
                "新 prototype 默认不可信：需人类在 mirror 中批准后才能被 spawnAgent 使用。"
                "behavior 是 v1 schema 的纯数据 EDN（只描述行为，无法授予工具）。"
                "script spec 可声明 :parameters 以支持 spawn 时参数化。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "prototype 名称（字母/数字/'-'/'_'，全局唯一）",
                    },
                    "behavior": {
                        "type": "string",
                        "description": (
                            "行为规约（EDN 字符串）。react: AgentSpec (trigger/context/loop/on-complete)；"
                            "script: ScriptSpec (:rules + 可选 :parameters，引用变量用 ${name})"
                        ),
                    },
                    "spec_type": {
                        "type": "string",
                        "enum": ["react", "script"],
                        "description": "规约类型：'react'（AgentSpec，默认）或 'script'（ScriptSpec，零 token daemon）",
                    },
                    "display_name": {
                        "type": "string",
                        "description": "显示名（人类在 mirror 审批时看到）",
                    },
                    "description": {
                        "type": "string",
                        "description": "一句话描述这个原型的行为定位",
                    },
                    "identity": {
                        "type": "string",
                        "description": "可选 IDENTITY.md 内容（仅 react；script 忽略）",
                    },
                    "soul": {
                        "type": "string",
                        "description": "可选 SOUL.md 内容（仅 react；script 忽略）",
                    },
                    "rules": {
                        "type": "string",
                        "description": "可选 RULES.md 内容（仅 react；script 忽略）",
                    },
                },
                "required": ["name", "behavior"],
            },
        )
        self._author_id = author_id

    async def execute(self, **kwargs: Any) -> ToolResult:
        name: str = kwargs["name"]
        behavior: str = kwargs["behavior"]
        spec_type: str = kwargs.get("spec_type", "react")
        display_name: str = kwargs.get("display_name", "")
        description: str = kwargs.get("description", "")
        identity: str = kwargs.get("identity", "")
        soul: str = kwargs.get("soul", "")
        rules: str = kwargs.get("rules", "")

        is_script = spec_type == "script"
        persona = (
            None
            if is_script
            else {"IDENTITY.md": identity, "SOUL.md": soul, "RULES.md": rules}
        )
        hint = _SCRIPT_HINT if is_script else _BEHAVIOR_HINT
        try:
            info = write_staging_prototype(
                name,
                behavior,
                persona=persona,
                display_name=display_name,
                description=description,
                author_id=self._author_id,
                spec_type=spec_type,
            )
        except PrototypeError as e:
            return ToolResult(
                success=False,
                data={"hint": hint},
                error=str(e),
                message=f"prototype 校验失败，未写入: {e}",
            )

        data: dict[str, Any] = {
            "name": info.name,
            "display_name": info.display_name,
            "trusted": info.trusted,
            "runtime": info.runtime,
            "status": "pending_approval",
        }
        return ToolResult(
            success=True,
            data=data,
            message=(
                f"prototype '{info.name}'（{info.runtime}）已写入 staging 区（不可信）。"
                "需人类在 mirror 的「规约审批」中批准后才能 spawn。"
            ),
        )
