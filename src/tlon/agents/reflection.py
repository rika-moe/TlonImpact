"""Reflection engine for Agent.

Agent self-driven memory audit: periodically snapshots memory state, asks
the LLM to analyze it, and applies the resulting operations (record/delete
claims). Extracted from the Agent god class.
"""

import asyncio
import contextlib
import json
from datetime import UTC, datetime
from importlib import resources
from typing import Any, cast

from tlon.agents.member_spec import MemberRule
from tlon.agents.session_journal import SessionJournal
from tlon.base.llm_client import LLMClient
from tlon.base.logger import get_logger
from tlon.memory.manager import MemoryManager

logger = get_logger(__name__)

#: The reflection audit's system prompt — policy text, kept as a readable
#: package data file (``tlon/agents/prompts/reflection_system.md``) instead
#: of a code constant; loaded once at import, fail-fast if missing.
REFLECTION_SYSTEM_PROMPT = (
    resources.files("tlon.agents")
    .joinpath("prompts/reflection_system.md")
    .read_text(encoding="utf-8")
    .strip()
)


class ReflectionEngine:
    """Runs background memory reflections for an agent.

    Owns the reflection scheduling state (interval, session counter,
    running flag). Initial enabled/interval come from the cognitive rule's
    loop (``rule.loop.reflect``, projected from the legacy ``:on-complete``
    section); ``enabled`` stays a runtime switch.

    The engine receives its services (memory, LLM client, session
    journal) and the cognitive rule directly — no back-reference to the
    owning member runtime.
    """

    def __init__(
        self,
        memory: MemoryManager,
        llm_client: LLMClient,
        rule: MemberRule,
        journal: SessionJournal,
    ):
        self._memory = memory
        self._llm_client = llm_client
        self._journal = journal

        # Reflection settings live on the cognitive rule's loop.
        assert rule.loop is not None, "ReflectionEngine requires a cognitive rule"
        reflect = rule.loop.reflect
        self.enabled: bool = reflect.enabled
        self._interval: float = reflect.interval_minutes * 60
        self._min_interactions: int = memory.config.reflection_min_interactions
        self._last_reflection_time: datetime | None = None
        self._session_count_since_reflection = 0
        self._running = False
        self._task: asyncio.Task[None] | None = None

    @property
    def is_running(self) -> bool:
        """A reflection (memory digest + LLM audit) is in flight right now.

        Read by the member runtime's idle accounting: an in-flight
        reflection is real background activity — the member must not be
        reported idle and drained mid-audit.
        """
        return self._running

    def note_interaction(self) -> None:
        """Record that a session completed (feeds the trigger)."""
        self._session_count_since_reflection += 1

    async def check_trigger(self) -> None:
        """Check if memory reflection should run and trigger if needed."""
        if not self.enabled:
            return
        if self._running:
            return

        now = datetime.now(UTC)
        if self._last_reflection_time:
            elapsed = (now - self._last_reflection_time).total_seconds()
            if elapsed < self._interval:
                return

        if self._session_count_since_reflection < self._min_interactions:
            return

        self._last_reflection_time = now
        self._session_count_since_reflection = 0
        self._running = True
        self._task = asyncio.create_task(self._run())

    async def shutdown(self) -> None:
        """Cancel an in-flight reflection and await it.

        Called from the member runtime's shutdown so no free-floating
        reflection task survives the member.
        """
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run(self) -> None:
        """Run a background memory reflection/audit.

        Takes a snapshot of memory state, asks LLM to analyze it, and applies
        the resulting operations (add/delete claims). Runs in a background
        task to avoid blocking the main loop.
        """
        try:
            digest = await self._memory.get_memory_digest(self._journal)

            if digest["claim_count"] == 0:
                logger.debug("reflection_skipped", reason="no_memory")
                return

            prompt = self._build_prompt(digest)
            response = await self._llm_client.chat(
                system_prompt=REFLECTION_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
                timeout=60.0,
                purpose="reflect",
            )

            if not response.content:
                return

            operations = self._parse_response(response.content)
            await self._apply_operations(operations)

            logger.info("reflection_completed", operation_count=len(operations))
        except Exception as e:
            logger.warning("reflection_failed", error=str(e))
        finally:
            self._running = False
            self._task = None

    def _build_prompt(self, digest: dict[str, Any]) -> str:
        """Build the reflection prompt from memory digest."""
        parts = ["## 当前记忆状态\n"]

        parts.append(f"未蒸馏会话数：{digest['undistilled_session_count']}")
        parts.append(f"记忆数：{digest['claim_count']}")
        parts.append("")

        if digest["claims"]:
            parts.append("### 记忆")
            for c in digest["claims"]:
                cid = str(c.get("id", ""))[:8]
                date = str(c.get("timestamp", ""))[:10]
                parts.append(f"- [{cid}] [{date}] {c.get('text', '')}")
            parts.append("")

        parts.append("")
        parts.append("请分析以上记忆状态，返回需要执行的 JSON 操作列表。")

        return "\n".join(parts)

    @staticmethod
    def _parse_response(content: str) -> list[dict[str, Any]]:
        """Parse LLM reflection response into operations list."""
        content = content.strip()
        if "```json" in content:
            start = content.find("```json") + 7
            end = content.find("```", start)
            if end > start:
                content = content[start:end].strip()
        elif "```" in content:
            start = content.find("```") + 3
            end = content.find("```", start)
            if end > start:
                content = content[start:end].strip()

        try:
            result = json.loads(content)
            return cast(list[dict[str, Any]], result.get("operations", []))
        except json.JSONDecodeError:
            logger.warning("reflection_parse_failed", content=content)
            return []

    async def _apply_operations(self, operations: list[dict[str, Any]]) -> None:
        """Apply reflection operations to memory."""
        for op in operations:
            action = op.get("action", "")
            try:
                if action == "record_claim":
                    await self._memory.remember_fact(
                        fact=op.get("description", ""),
                        importance=float(op.get("importance", 0.5)),
                    )
                    logger.debug("reflection_claim_recorded", description=op.get("description", ""))
                elif action == "delete_claim":
                    claim_id = op.get("claim_id", "")
                    if claim_id:
                        deleted = await self._memory.delete_claim(claim_id)
                        if deleted:
                            logger.debug("reflection_claim_deleted", claim_id=claim_id)
                        # Not found / ambiguous: already logged by the manager.
                    else:
                        description = op.get("description", "")
                        if description:
                            result = await self._memory.forget(
                                query=description, confirm=True
                            )
                            logger.debug(
                                "reflection_claim_deleted_by_description",
                                description=description,
                                result=result,
                            )
                        else:
                            logger.warning("reflection_delete_claim_no_target", operation=op)
                elif action == "noop":
                    pass
                else:
                    logger.debug("reflection_action_unknown", action=action)
            except Exception as e:
                logger.warning("reflection_action_failed", action=action, error=str(e))
