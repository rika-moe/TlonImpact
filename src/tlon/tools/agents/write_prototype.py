"""Write an LLM-authored behavior prototype to the staging area (stage 4a).

Supports both react (cognitive loop rule) and script (deterministic rules)
prototypes — one unified rules-form format (phase C).
"""

from typing import Any

from tlon.agents.prototypes import PrototypeError, write_staging_prototype
from tlon.base.logger import get_logger
from tlon.tools.base import Tool, ToolResult

logger = get_logger(__name__)

_BEHAVIOR_HINT = (
    "behavior 必须是 v1 schema 的 EDN map：:version 1 必填；"
    ":rules [{:name str :on event-keyword :when (纯谓词表达式，可选) "
    ":loop {:context {:sections [(persona) (protocol) (knowledge) (mail-history N)]} "
    ":stages [(think) (act)] "
    ":until (or (iteration-limit-reached?) (error-limit-reached?)) "
    ":max-iterations int :max-consecutive-errors int :save-interaction bool "
    ":reflect {:enabled bool :interval-minutes int}} "
    ":actions [(动作表达式) ...]} ...]。认知规则（带 :loop）的默认命中谓词是 "
    "(or (addressed-to-me? msg) (cc-ed? msg))。"
    "可混排（cost routing）：一份 spec 至多一条带 :loop 的认知规则，可外加若干"
    "确定性规则（无 :loop）在前——确定性规则先处理便宜路径，认知规则的 :when "
    "用 (not ...) 排除它已处理的（deflection）。"
    "可选 :parameters 声明模板变量（:type :string 或 :string-list），引用时用 ${变量名}。"
    "可选 :identity / :soul / :rules 为人格文件内容。"
)

_SCRIPT_HINT = (
    "behavior 必须是 v1 schema 的 EDN map：:version 1 必填；"
    ":rules [{:name str :on event-keyword-or-:boot "
    ":when (纯谓词表达式，可选) :actions [(动作表达式) ...]} ...]。"
    "确定性规则（无 :loop）：命中后直接执行 :actions（start/stop/forward）。"
    "可选 :parameters 声明模板变量（:type :string 或 :string-list），"
    "引用时用 ${变量名}。确定性模板不需要 persona 文件。"
)


class WritePrototypeTool(Tool):
    """Tool to author a new behavior prototype (pure-data spec + persona).

    Accepts both react specs (a cognitive rule with a loop) and script
    specs (loop-less deterministic rules) — the output is one unified
    rules-form behavior.tlon either way; ``spec_type`` only picks the
    template (react writes persona files alongside, script is a
    persona-less daemon). The prototype lands in
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
                "支持 spec_type='react'（默认，认知规则——带 :loop 的规则形态）"
                "和 spec_type='script'（确定性规则——无 :loop，零 token daemon）。"
                "两种都输出统一的 rules-form behavior.tlon。"
                "新 prototype 默认不可信：需人类在 mirror 中批准后才能被 spawnAgent 使用。"
                "behavior 是 v1 schema 的纯数据 EDN（只描述行为，无法授予工具）。"
                "确定性规则可声明 :parameters 以支持 spawn 时参数化。"
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
                            "行为规约（EDN 字符串）。统一 rules-form：react 是带 :loop 的"
                            "认知规则，script 是纯确定性规则（可选 :parameters，引用变量用 ${name}）"
                        ),
                    },
                    "spec_type": {
                        "type": "string",
                        "enum": ["react", "script"],
                        "description": "模板类型：'react'（认知规则 + 人格文件，默认）或 'script'（确定性规则，零 token daemon）",
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
