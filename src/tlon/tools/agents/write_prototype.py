"""Write an LLM-authored behavior prototype to the staging area (stage 4a).

All rules use one event-driven program format
(phase C). Unified member model (ADR 0006): prototypes carry no persona —
a member's persona files are seeded at creation and member-owned
afterwards (self-editable via ``updatePersona``, ADR 0024), never part of
the behavior genome.
"""

from collections.abc import Awaitable, Callable
from typing import Any

from tlon.base.logger import get_logger
from tlon.tools.base import Tool, ToolResult
from tlon.tools.prototypes.ports import PrototypeError, PrototypeSummary

logger = get_logger(__name__)

_BEHAVIOR_HINT = (
    "behavior 必须是 v1 EDN map：{:version 1 :rules [{:name str :on event-keyword-or-:boot "
    ":when (可选纯谓词) :actions [(动作表达式) ...]} ...]}。"
    "所有规则使用相同的执行机制，普通 if、while、函数可以组合动作。"
    "需要运行会话时在动作中使用 (run-loop (quote {:context {:sections [(persona) (protocol) "
    "(lessons) (skills) (knowledge) (current-task) (mail-history 20) (thread-recap)]} "
    ":stages [(think) (act)] :until (or (iteration-limit-reached?) (error-limit-reached?) "
    "(token-limit-reached?)) :max-iterations 10 :max-consecutive-errors 3 :max-tokens 1000000 "
    ":reflect {:enabled false :interval-minutes 15}}))。"
    "会话是普通效果请求，可由任意规则调用；规则顶层没有 :loop。"
    "forward 使用实际触发邮件；mark-read 显式标读触发邮件。"
    "可选 :parameters 声明模板变量（:type :string 或 :string-list），引用时用 ${变量名}。"
)

# Signature of the injected near-kin check (): (behavior text,
# excluded parent name) in, (closest prototype name, similarity ratio)
# out when the proposal is a reskin-level cross-bloodline duplicate,
# else None.
NearKinCheck = Callable[[str, str | None], "tuple[str, float] | None"]


def _check_near_kin(
    near_kin: NearKinCheck | None, behavior: str, parent: str | None
) -> ToolResult | None:
    """Near-kin rejection (): a reskin-level duplicate is turned away.

    ``parent`` is exempted from the comparison — a child resembling its
    own parent is the generation channel working as intended; the gate
    rejects cross-bloodline convergence. Fail-open like the solvency gate
    (): a missing provider or any provider failure yields None (no
    rejection) — similarity bookkeeping must never block a proposal.
    """
    if near_kin is None:
        return None
    try:
        hit = near_kin(behavior, parent)
    except Exception as e:
        logger.warning("near_kin_check_failed", error=str(e))
        return None
    if hit is None:
        return None
    kin_name, ratio = hit
    return ToolResult(
        success=False,
        data={},
        error=f"near-kin of prototype '{kin_name}' (similarity {ratio:.2f})",
        message=(f"与 '{kin_name}' 相似度 {ratio:.2f}，请做出实质差异或改为提案改进它"),
    )


class WritePrototypeTool(Tool):
    """Tool to author a new behavior prototype (pure-data spec).

    Accepts one unified rules-form behavior.tlon program. Prototypes carry no persona (unified member
    model, ADR 0006). The prototype lands in
    ``~/.tlon/prototypes-staging/`` marked untrusted: it cannot be spawned
    until a human approves it (ops/WebUI — the Gatekeeper model of
    stage 4a). Validation errors come back verbatim so the LLM can fix
    its EDN and retry.
    """

    def __init__(
        self,
        author_id: str,
        writer: Callable[..., PrototypeSummary],
        prototype: str | None = None,
        solvency: Callable[[str], Awaitable[bool]] | None = None,
        near_kin: NearKinCheck | None = None,
    ) -> None:
        super().__init__(
            name="writePrototype",
            description=(
                "创作一个新的 behavior prototype（纯行为规约，无人格），写入 staging 区。"
                "规则统一使用 on、when、actions；会话通过 run-loop 动作调用。"
                "新 prototype 默认不可信：需人类批准（ops/WebUI）后才能被 spawnAgent 使用。"
                "behavior 是 v1 schema 的纯数据 EDN（只描述行为，无法授予工具）。"
                "可声明 :parameters 以支持 spawn 时参数化。"
                "可选 parent 记录谱系（从哪个既有原型派生），generation 由服务端推导。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "behavior": {
                        "type": "string",
                        "description": (
                            "行为规约（EDN 字符串）。统一 on/when/actions；会话使用 run-loop 动作。"
                            "可选 :parameters，引用变量用 ${name}。"
                        ),
                    },
                    "display_name": {
                        "type": "string",
                        "description": "必填行为标签，描述怎样行动；不赋予职业、职位或社会身份",
                    },
                    "description": {
                        "type": "string",
                        "description": "一句话描述这个原型的行为定位",
                    },
                    "parent": {
                        "type": "string",
                        "description": (
                            "父原型系统 ID（本原型从哪个原型派生而来；留空表示新的根原型）。"
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
                            "随包教训：退休成员蒸馏出的经验，落盘为原型的 "
                            "LESSONS.md；实例化时拷贝给新成员，由 spec 的 (lessons) "
                            "context section 注入对话"
                        ),
                    },
                },
                "required": ["behavior", "display_name"],
            },
        )
        self._author_id = author_id
        self._writer = writer
        # Author's bloodline + solvency gate (): an insolvent
        # bloodline loses proposal eligibility. None (either side) =
        # always eligible.
        self._prototype = prototype
        self._solvency = solvency
        # Near-kin rejection (, agent side only): a reskin-level
        # duplicate of an existing prototype is turned away. None =
        # no check.
        self._near_kin = near_kin

    async def _solvent(self, prototype: str) -> bool:
        """Proposal-eligibility check for the author's bloodline (fail-open).

        A missing provider or any provider failure yields True — solvency
        bookkeeping must never block a proposal.
        """
        if self._solvency is None:
            return True
        try:
            return bool(await self._solvency(prototype))
        except Exception as e:
            logger.warning("solvency_check_failed", prototype=prototype, error=str(e))
            return True

    async def execute(self, **kwargs: Any) -> ToolResult:
        if "name" in kwargs:
            return ToolResult(
                success=False,
                data={},
                error="name is system-generated; provide display_name instead",
            )
        if not isinstance(kwargs.get("display_name"), str) or not kwargs["display_name"].strip():
            return ToolResult(
                success=False, data={}, error="display_name must be a non-empty behavior label"
            )
        if self._prototype is not None and not await self._solvent(self._prototype):
            return ToolResult(
                success=False,
                data={},
                error=f"bloodline '{self._prototype}' is insolvent",
                message=(
                    f"血脉 '{self._prototype}' 已失去偿付能力（长期入不敷出），提案资格被暂停。"
                    "先用手上成员做出被 endorse 的产出，恢复偿付能力后再提案。"
                ),
            )

        behavior: str = kwargs["behavior"]
        display_name: str = kwargs["display_name"]
        description: str = kwargs.get("description", "")
        parent: str | None = kwargs.get("parent") or None
        proposal_note: str | None = kwargs.get("proposal_note") or None
        lessons: str | None = kwargs.get("lessons") or None

        hint = _BEHAVIOR_HINT
        # Near-kin check (): the exempted parent is the explicit
        # ``parent`` argument when given, else the author's own prototype
        # (explicit parent > own prototype).
        near_kin_rejection = _check_near_kin(self._near_kin, behavior, parent or self._prototype)
        if near_kin_rejection is not None:
            return near_kin_rejection
        try:
            info = self._writer(
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
                "需人类在「规约审批」（ops/WebUI）中批准后才能 spawn。"
            ),
        )
