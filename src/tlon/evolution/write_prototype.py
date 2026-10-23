"""Write an LLM-authored behavior prototype to the staging area (stage 4a).

All rules use one event-driven program format
(phase C). Unified member model (ADR 0006): prototypes carry no persona —
a member's persona files are seeded at creation and member-owned
afterwards (self-editable via ``persona-update``, ADR 0024), never part of
the behavior genome.
"""

from collections.abc import Awaitable, Callable
from typing import Any

from tlon.base.logger import get_logger
from tlon.evolution.ports import PrototypeError, PrototypeSummary
from tlon.operations.schema import RejectedError

logger = get_logger(__name__)

_BEHAVIOR_HINT = (
    "输出完整 v2 程序包：behavior 为 EDN {:version 2 :handlers {:mail-delivered work.reply/handle} "
    ':fuel 10000}；modules 为相对路径到完整源码的对象，例如 "work/reply.tlon"。'
    "模块首句 (ns work.reply (:require [work.helpers :as h]) (:export handle))，"
    "导出 (defn handle [event] ...)，入口参数 event 含 :sender 和 :mail（:content 等）。所有依赖必须随包提交；"
    "宿主 tlon.* 不可重定义。可 require tlon.messages、tlon.conversation、tlon.context、tlon.workflows。"
    '模型调用 (model-call {:messages [{:role "user" :content "任务"}] :tools [] :max_tokens 128})，'
    "返回 :content/:tool_calls/:usage；模型不会自动执行工具。"
    '对话 (conversation/run {:messages messages :tools ["time-now"] :max_tokens 128 :max_rounds 3}) '
    '返回 :content/:messages/:reason。tlon.workflows 导出 short-turn、plan-then-act、evidence-check、reviewed、route，均接收与 conversation/run 相同的 options map。发信 (mail-send {:to ["human:alice"] :content text})。'
    "不得使用 run-loop、stage、context section 或 v1 rules。"
)


def bundle_text(behavior: str, modules: dict[str, str]) -> str:
    """Canonical full author-source text for similarity and review."""
    return (
        behavior
        + "\n"
        + "\n".join(f"modules/{path}\n{source}" for path, source in sorted(modules.items()))
    )


def require_bundle(behavior: Any, modules: Any) -> tuple[str, dict[str, str]]:
    """Machine authors submit complete v2 sources; staging validates linking."""
    from tlosure.edn import loads

    if not isinstance(behavior, str) or not isinstance(modules, dict) or not modules:
        raise RejectedError(
            "完整 v2 程序包需要 behavior 字符串及非空 modules 对象", {"hint": _BEHAVIOR_HINT}
        )
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in modules.items()):
        raise RejectedError("modules 必须是路径到源码字符串的对象", {})
    try:
        version = loads(behavior).get("version")
    except (ValueError, AttributeError) as error:
        raise RejectedError("behavior 必须是 v2 EDN map", {}) from error
    if version != 2:
        raise RejectedError("机器提案只接受 v2 程序包", {"hint": _BEHAVIOR_HINT})
    return behavior, dict(modules)


# Signature of the injected near-kin check (): (behavior text,
# excluded parent name) in, (closest prototype name, similarity ratio)
# out when the proposal is a reskin-level cross-bloodline duplicate,
# else None.
NearKinCheck = Callable[[str, str | None], "tuple[str, float] | None"]


def _check_near_kin(near_kin: NearKinCheck | None, behavior: str, parent: str | None) -> None:
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
    raise RejectedError(f"near-kin of prototype '{kin_name}' (similarity {ratio:.2f})", {})


class PrototypeWriter:
    """Service to author a new behavior prototype (pure-data spec).

    Accepts a complete v2 behavior.tlon and module source package. Prototypes carry no persona (unified member
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

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        if "name" in kwargs:
            raise RejectedError("name is system-generated; provide display_name instead", {})
        if not isinstance(kwargs.get("display_name"), str) or not kwargs["display_name"].strip():
            raise RejectedError("display_name must be a non-empty behavior label", {})
        if self._prototype is not None and not await self._solvent(self._prototype):
            raise RejectedError(f"bloodline '{self._prototype}' is insolvent", {})

        behavior, modules = require_bundle(kwargs.get("behavior"), kwargs.get("modules"))
        display_name: str = kwargs["display_name"]
        description: str = kwargs.get("description", "")
        parent: str | None = kwargs.get("parent") or None
        proposal_note: str | None = kwargs.get("proposal_note") or None
        lessons: str | None = kwargs.get("lessons") or None

        hint = _BEHAVIOR_HINT
        # Near-kin check (): the exempted parent is the explicit
        # ``parent`` argument when given, else the author's own prototype
        # (explicit parent > own prototype).
        _check_near_kin(self._near_kin, bundle_text(behavior, modules), parent or self._prototype)
        try:
            info = self._writer(
                behavior,
                modules=modules,
                display_name=display_name,
                description=description,
                author_id=self._author_id,
                parent=parent,
                proposal_note=proposal_note,
                lessons=lessons,
            )
        except PrototypeError as e:
            raise RejectedError(str(e), {"hint": hint}) from e

        data: dict[str, Any] = {
            "name": info.name,
            "display_name": info.display_name,
            "trusted": info.trusted,
            "parent": info.parent,
            "generation": info.generation,
            "status": "pending_approval",
        }
        return data
