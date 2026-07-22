"""Reflection engine for Agent.

Agent self-driven memory audit: periodically snapshots memory state, asks
the LLM to analyze it, and applies the resulting operations (record/delete
facts, trigger consolidation). Extracted from the Agent god class.
"""

import asyncio
import json
from datetime import datetime
from typing import TYPE_CHECKING, Any

from tlon.base.logger import get_logger

if TYPE_CHECKING:
    from tlon.agents.agent import Agent

logger = get_logger(__name__)


class ReflectionEngine:
    """Runs background memory reflections for an agent.

    Owns the reflection scheduling state (interval, interaction counter,
    running flag). Reads its tunables from the memory manager's config.
    """

    REFLECTION_SYSTEM_PROMPT = (
        "你是一个记忆梳理助手。你的任务是审视一个 Agent 的持久记忆，"
        "发现冗余、矛盾、过时或遗漏的信息，并决定需要执行的操作。\n\n"
        "可用的操作类型：\n"
        '- record_fact: 记录从对话中推断出的新事实。需要提供 description（自然语言描述）、'
        "fact_type（USER_PREFERENCE/PROJECT_STATE/TECH_STACK/DECISION/GENERAL）、"
        "subject、predicate、object、confidence（0.0-1.0）。\n"
        "- delete_fact: 删除过时、冗余或矛盾的事实。需要提供 fact_id 或 description 用于匹配。\n"
        "- consolidate: 如果工作记忆中有未处理的交互，请求触发摘要生成。\n"
        "- noop: 当前记忆状态良好，无需操作。\n\n"
        "原则：\n"
        "1. 只记录明确、重要的信息，不要推测\n"
        "2. 发现两个事实描述相同内容时，只保留更完整的一个\n"
        "3. 发现事实互相矛盾时，删除置信度更低的一个\n"
        "4. 如果工作记忆中有 >= 5 条交互且没有近期摘要，建议 consolidate\n"
        "5. 多数情况下不需要操作，noop 是正常的\n\n"
        "请用 JSON 格式返回："
        '{"analysis": "对当前记忆状态的简要分析", '
        '"operations": [{"action": "...", ...}]}'
    )

    def __init__(self, agent: "Agent"):
        self._agent = agent

        memory_config = agent.memory.config
        self.enabled: bool = getattr(memory_config, "reflection_enabled", True)
        self._interval: float = getattr(memory_config, "reflection_interval_minutes", 15) * 60
        self._min_interactions: int = getattr(
            memory_config, "reflection_min_interactions", 5
        )
        self._last_reflection_time: datetime | None = None
        self._interaction_count_since_reflection = 0
        self._running = False

    def note_interaction(self) -> None:
        """Record that an interaction was saved to memory (feeds the trigger)."""
        self._interaction_count_since_reflection += 1

    async def check_trigger(self) -> None:
        """Check if memory reflection should run and trigger if needed."""
        if not self.enabled:
            return
        if self._running:
            return

        now = datetime.now()
        if self._last_reflection_time:
            elapsed = (now - self._last_reflection_time).total_seconds()
            if elapsed < self._interval:
                return

        if self._interaction_count_since_reflection < self._min_interactions:
            return

        self._last_reflection_time = now
        self._interaction_count_since_reflection = 0
        self._running = True
        asyncio.create_task(self._run())

    async def _run(self) -> None:
        """Run a background memory reflection/audit.

        Takes a snapshot of memory state, asks LLM to analyze it, and applies
        the resulting operations (add/delete facts, trigger consolidation).
        Runs in a background task to avoid blocking the main loop.
        """
        try:
            digest = await self._agent.memory.get_memory_digest()

            if digest["working_count"] == 0 and digest["summary_count"] == 0 and digest["fact_count"] == 0:
                logger.debug("reflection_skipped", reason="no_memory")
                return

            prompt = self._build_prompt(digest)
            response = await self._agent.llm_client.chat(
                system_prompt=self.REFLECTION_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                timeout=60.0,
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

    def _build_prompt(self, digest: dict[str, Any]) -> str:
        """Build the reflection prompt from memory digest."""
        parts = ["## 当前记忆状态\n"]

        parts.append(f"工作记忆交互数：{digest['working_count']}")
        parts.append(f"会话摘要数：{digest['summary_count']}")
        parts.append(f"已知事实数：{digest['fact_count']}")
        if digest["last_interaction_time"]:
            parts.append(f"最后交互时间：{digest['last_interaction_time'][:19]}")
        parts.append("")

        if digest["summaries"]:
            parts.append("### 会话摘要")
            for s in digest["summaries"][-10:]:
                text = s.get("summary_text", "")[:200]
                ts = s.get("timestamp", "")[:19]
                parts.append(f"- [{ts}] {text}")
            parts.append("")

        if digest["facts"]:
            parts.append("### 已知事实")
            for f in digest["facts"]:
                fid = f.get("id", "")[:8]
                desc = f.get("description", "")
                if not desc:
                    desc = (
                        f"{f.get('subject', '')} {f.get('predicate', '')} "
                        f"{f.get('object', '')}"
                    )
                ftype = f.get("fact_type", "GENERAL")
                conf = f.get("confidence", 0.5)
                parts.append(f"- [{fid}] [{ftype}] {desc} (置信度:{conf:.1f})")
            parts.append("")

        parts.append("### 工作记忆概览")
        if digest["working_count"] > 0:
            parts.append(f"有 {digest['working_count']} 条交互待处理")
        else:
            parts.append("工作记忆为空")

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
            return result.get("operations", [])
        except json.JSONDecodeError:
            logger.warning("reflection_parse_failed", content=content)
            return []

    async def _apply_operations(self, operations: list[dict[str, Any]]) -> None:
        """Apply reflection operations to memory."""
        for op in operations:
            action = op.get("action", "")
            try:
                if action == "record_fact":
                    await self._agent.memory.remember_fact(
                        fact=op.get("description", ""),
                        fact_type=op.get("fact_type", "GENERAL"),
                        subject=op.get("subject", "用户"),
                        predicate=op.get("predicate", "提到"),
                        object=op.get("object", ""),
                        importance=float(op.get("confidence", 0.5)),
                    )
                    logger.debug("reflection_fact_recorded", description=op.get("description", ""))
                elif action == "delete_fact":
                    query = op.get("description") or op.get("fact_id", "")
                    if query:
                        await self._agent.memory.forget(query=query, confirm=True)
                        logger.debug("reflection_fact_deleted", query=query)
                elif action == "consolidate":
                    await self._agent.memory.generate_summary(force=True)
                    logger.debug("reflection_consolidation_triggered")
                elif action == "noop":
                    pass
                else:
                    logger.debug("reflection_action_unknown", action=action)
            except Exception as e:
                logger.warning("reflection_action_failed", action=action, error=str(e))
