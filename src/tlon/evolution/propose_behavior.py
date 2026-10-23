"""Propose a next-generation behavior from lived experience (see
docs/design/prototypes.md).

``behavior-propose`` is the experience entry of the generation channel:
unlike ``prototype-write`` (a bare write channel where the caller supplies
the full spec text), this tool drafts the new spec itself — one dedicated
LLM call is fed the member's own ``config/behavior.tlon``, its memory
digest and distilled lessons, plus the caller's change intent. The draft
then lands in the staging area through the same ``write_staging_prototype``
gate, parented to the member's own prototype, so the trust model is
unchanged: nothing spawns until a human approves.

No retry inside the tool — validation/JSON parsing failures come back
verbatim and the outer ReAct loop is the retry mechanism (the
``prototype-write`` precedent).
"""

import json
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from tlon.base.llm_client import LLMClient
from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths
from tlon.evolution.ports import PrototypeError, PrototypeSummary
from tlon.evolution.write_prototype import (
    _BEHAVIOR_HINT,
    NearKinCheck,
    _check_near_kin,
    bundle_text,
    require_bundle,
)
from tlon.operations.schema import RejectedError

if TYPE_CHECKING:
    from tlon.memory.manager import MemoryManager, SessionLogReader

logger = get_logger(__name__)

_DRAFT_SYSTEM_PROMPT = (
    "你是工作程序起草器。根据当前完整源码、记忆、教训与变更意图起草完整新程序。"
    '只输出严格 JSON 对象 {"behavior":"完整 EDN","modules":{"相对路径":"完整源码"}}，'
    "不输出解释或 Markdown，不输出 diff。保留无关行为；至少拆分入口和可复用工作函数两个模块。"
    "原型不授予身份或工具权限。" + _BEHAVIOR_HINT
)


def _render_digest(digest: dict[str, Any]) -> str:
    """Render a memory digest as prompt text (claims)."""
    parts = [
        f"未蒸馏会话数：{digest['undistilled_session_count']}；记忆数：{digest['claim_count']}",
    ]
    if digest["claims"]:
        parts.append("### 记忆")
        for c in digest["claims"]:
            cid = str(c.get("id", ""))[:8]
            date = str(c.get("timestamp", ""))[:10]
            parts.append(f"- [{cid}] [{date}] {c.get('text', '')}")
    return "\n".join(parts)


class BehaviorProposer:
    """Service to propose a next-generation behavior from lived experience.

    The member supplies only the change intent and a behavior label; the tool reads
    its own complete behavior/module sources and memory, drafts a complete new package with one
    LLM call (purpose="propose"), and writes it to the staging area
    parented to the member's own prototype (``AgentConfig.prototype``;
    None = a new root). The proposal is untrusted: a human approves it in
    human review (ops/WebUI), then ``member-spawn`` derives the next generation.
    """

    def __init__(
        self,
        agent_id: str,
        prototype: str | None,
        author_id: str,
        llm_client: LLMClient,
        memory: "MemoryManager",
        logs: "SessionLogReader",
        writer: Callable[..., PrototypeSummary],
        tlon_paths: TlonPaths,
        solvency: Callable[[str], Awaitable[bool]] | None = None,
        near_kin: NearKinCheck | None = None,
    ) -> None:
        self._agent_id = agent_id
        self._prototype = prototype
        self._author_id = author_id
        self._llm_client = llm_client
        self._memory = memory
        self._logs = logs
        self._writer = writer
        self._paths = tlon_paths
        # Solvency gate (): an insolvent bloodline loses proposal
        # eligibility. None = always eligible.
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

        intent: str = kwargs["intent"]
        display_name: str = kwargs["display_name"]
        description: str = kwargs.get("description", "")

        behavior_file = self._paths.agent_behavior_file(self._agent_id)
        if not behavior_file.exists():
            raise RejectedError(f"member '{self._agent_id}' has no behavior.tlon", {})
        base_spec = behavior_file.read_text(encoding="utf-8")

        module_dir = behavior_file.parent / "modules"
        modules: dict[str, str] = {}
        if module_dir.is_symlink():
            raise RejectedError("当前模块目录不能是符号链接", {})
        if module_dir.exists():
            for path in sorted(module_dir.rglob("*")):
                if path.is_symlink():
                    raise RejectedError("当前模块包不能含符号链接", {})
                if path.is_file():
                    if path.suffix != ".tlon":
                        raise RejectedError("当前模块包只能包含 .tlon 源文件", {})
                    modules[path.relative_to(module_dir).as_posix()] = path.read_text(
                        encoding="utf-8"
                    )

        digest = await self._memory.get_memory_digest(self._logs)
        lessons_file = self._paths.memory_lessons_file(self._agent_id)
        lessons = lessons_file.read_text(encoding="utf-8") if lessons_file.exists() else None

        prompt_parts = [
            "## 当前完整程序包\n"
            + json.dumps({"behavior": base_spec, "modules": modules}, ensure_ascii=False),
            "## 个体经验\n" + _render_digest(digest),
        ]
        if lessons:
            prompt_parts.append("## 蒸馏教训（lessons.md）\n" + lessons)
        prompt_parts.append("## 变更意图\n" + intent)

        response = await self._llm_client.chat(
            system_prompt=_DRAFT_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": "\n\n".join(prompt_parts)}],
            timeout=60.0,
            purpose="propose",
        )

        try:
            package = json.loads(response.content or "")
        except json.JSONDecodeError as error:
            raise RejectedError("起草调用必须输出严格 JSON 程序包", {}) from error
        if not isinstance(package, dict) or set(package) != {"behavior", "modules"}:
            raise RejectedError("起草程序包必须且只能包含 behavior 和 modules", {})
        draft, draft_modules = require_bundle(package["behavior"], package["modules"])
        if len(draft_modules) < 2:
            raise RejectedError("起草程序必须包含入口及可复用工作函数至少两个模块", {})

        hint = _BEHAVIOR_HINT
        # Near-kin rejection () sits after drafting: the draft text is
        # the thing being compared, so the check cannot run earlier. The
        # proposal's parent is always the member's own prototype, which is
        # exempt from the comparison.
        _check_near_kin(self._near_kin, bundle_text(draft, draft_modules), self._prototype)
        try:
            info = self._writer(
                draft,
                modules=draft_modules,
                display_name=display_name,
                description=description,
                author_id=self._author_id,
                parent=self._prototype,
                proposal_note=intent,
                lessons=lessons,
            )
        except PrototypeError as e:
            raise RejectedError(str(e), {"hint": hint}) from e

        logger.info(
            "behavior_proposed",
            agent_id=self._agent_id,
            name=info.name,
            parent=info.parent,
            generation=info.generation,
        )
        return {
            "name": info.name,
            "parent": info.parent,
            "generation": info.generation,
            "trusted": info.trusted,
            "status": "pending_approval",
        }
