"""Write an LLM-authored behavior prototype to the staging area (stage 4a).

Supports both rule forms — cognitive rules carrying a ``:loop`` session
and loop-less deterministic rules — in one unified rules-form format
(phase C). Unified member model (ADR 0006): prototypes carry no persona —
a member's persona files are creation-time data, never part of the
behavior genome.
"""

from collections.abc import Callable
from typing import Any

from tlon.base.logger import get_logger
from tlon.tools.base import Tool, ToolResult
from tlon.tools.prototypes.ports import PrototypeError, PrototypeSummary

logger = get_logger(__name__)

_BEHAVIOR_HINT = (
    "behavior 必须是 v1 schema 的 EDN map：:version 1 必填；"
    ":rules [{:name str :on event-keyword :when (纯谓词表达式，可选) "
    ":loop {:context {:sections [(persona) (protocol) (knowledge) (skills) (mail-history N)]} "
    ":stages [(think) (act)] "
    ":until (or (iteration-limit-reached?) (error-limit-reached?)) "
    ":max-iterations int :max-consecutive-errors int "
    ":reflect {:enabled bool :interval-minutes int}} "
    ":actions [(动作表达式) ...]} ...]。认知规则（带 :loop）的默认命中谓词是 "
    "(or (addressed-to-me? msg) (cc-ed? msg))。"
    "可混排（cost routing）：一份 spec 至多一条带 :loop 的认知规则，可外加若干"
    "确定性规则（无 :loop）在前——确定性规则先处理便宜路径，认知规则的 :when "
    "用 (not ...) 排除它已处理的（deflection）。"
    "可选 :parameters 声明模板变量（:type :string 或 :string-list），引用时用 ${变量名}。"
)

_SCRIPT_HINT = (
    "behavior 必须是 v1 schema 的 EDN map：:version 1 必填；"
    ":rules [{:name str :on event-keyword-or-:boot "
    ":when (纯谓词表达式，可选) :actions [(动作表达式) ...]} ...]。"
    "确定性规则（无 :loop）：命中后直接执行 :actions（start/stop/forward）。"
    "可选 :parameters 声明模板变量（:type :string 或 :string-list），"
    "引用时用 ${变量名}。"
)


class WritePrototypeTool(Tool):
    """Tool to author a new behavior prototype (pure-data spec).

    Accepts both rule forms (a cognitive rule with a ``:loop``, or
    loop-less deterministic rules) — the output is one unified rules-form
    behavior.tlon either way. Prototypes carry no persona (unified member
    model, ADR 0006). The prototype lands in
    ``~/.tlon/prototypes-staging/`` marked untrusted: it cannot be spawned
    until a human approves it in the mirror (the Gatekeeper model of
    stage 4a). Validation errors come back verbatim so the LLM can fix
    its EDN and retry.
    """

    def __init__(
        self, author_id: str, writer: Callable[..., PrototypeSummary]
    ) -> None:
        super().__init__(
            name="writePrototype",
            description=(
                "创作一个新的 behavior prototype（纯行为规约，无人格），写入 staging 区。"
                "两种规则形态都输出统一的 rules-form behavior.tlon："
                "带 :loop 的规则（挂认知循环）与无 :loop 的规则（命中后直接执行 :actions）。"
                "新 prototype 默认不可信：需人类在 mirror 中批准后才能被 spawnAgent 使用。"
                "behavior 是 v1 schema 的纯数据 EDN（只描述行为，无法授予工具）。"
                "无 :loop 的规则可声明 :parameters 以支持 spawn 时参数化。"
                "可选 parent 记录谱系（从哪个既有原型派生），generation 由服务端推导。"
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
                            "行为规约（EDN 字符串）。统一 rules-form：带 :loop 的规则是"
                            "认知规则，无 :loop 的是确定性规则（可选 :parameters，引用变量用 ${name}）"
                        ),
                    },
                    "display_name": {
                        "type": "string",
                        "description": "显示名（人类在 mirror 审批时看到）",
                    },
                    "description": {
                        "type": "string",
                        "description": "一句话描述这个原型的行为定位",
                    },
                    "parent": {
                        "type": "string",
                        "description": (
                            "父原型名称（本原型从哪个原型派生而来；留空表示新的根原型）。"
                            "父原型必须已存在（trusted 库或 staging 区皆可），"
                            "generation 由服务端自动推导为父代 + 1"
                        ),
                    },
                    "proposal_note": {
                        "type": "string",
                        "description": "本代变更说明：相对父原型改了什么、为什么",
                    },
                    "lessons": {
                        "type": "string",
                        "description": (
                            "随包教训（issue ）：退休成员蒸馏出的经验，落盘为原型的 "
                            "LESSONS.md；实例化时拷贝给新成员，由 spec 的 (lessons) "
                            "context section 注入对话"
                        ),
                    },
                },
                "required": ["name", "behavior"],
            },
        )
        self._author_id = author_id
        self._writer = writer

    async def execute(self, **kwargs: Any) -> ToolResult:
        name: str = kwargs["name"]
        behavior: str = kwargs["behavior"]
        display_name: str = kwargs.get("display_name", "")
        description: str = kwargs.get("description", "")
        parent: str | None = kwargs.get("parent") or None
        proposal_note: str | None = kwargs.get("proposal_note") or None
        lessons: str | None = kwargs.get("lessons") or None

        hint = _BEHAVIOR_HINT if ":loop" in behavior else _SCRIPT_HINT
        try:
            info = self._writer(
                name,
                behavior,
                display_name=display_name,
                description=description,
                author_id=self._author_id,
                parent=parent,
                proposal_note=proposal_note,
                lessons=lessons,
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
            "parent": info.parent,
            "generation": info.generation,
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
