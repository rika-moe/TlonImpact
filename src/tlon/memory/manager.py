"""Memory Manager - the episodic memory interface (ADR 0009).

The working-memory layer is retired: the session journal (ADR 0007) is
the sole interaction truth, and summarization flows journal -> episodic
directly. The manager keeps the episodic store (summaries + facts), the
context-block assembly for the ``(knowledge)`` section, and the batch
summary trigger the scheduler calls at its session reap point.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

import aiofiles

from tlon.base.config import LLMConfig
from tlon.base.logger import get_logger
from tlon.base.paths import paths

from .episodic_store import EpisodicMemoryStore
from .models import (
    ContextBlock,
    Fact,
    FactType,
    MemoryConfig,
    SessionProjection,
    Summary,
)
from .summarizer import Summarizer

logger = get_logger(__name__)


class SessionLogReader(Protocol):
    """The journal read surface the summary trigger needs.

    ``tlon.memory`` cannot import ``tlon.agents`` (import-linter), so the
    journal (agents layer) is injected structurally: any object with
    these two methods qualifies — in production, ``SessionJournal``.
    """

    async def list_completed(self) -> list[str]:
        """Session ids whose log carries a ``session/end``."""
        ...

    async def read_events(self, session_id: str) -> list[dict[str, Any]]:
        """All events of one session's log, in order."""
        ...


class MemoryManager:
    """Main interface for Agent memory management.

    Owns Episodic Memory (summaries and facts), builds the context
    blocks injected into the LLM conversation, and runs the batch
    summary trigger (journal -> episodic).

    Usage:
        >>> manager = MemoryManager("dev-001", config, Path("./data"))
        >>> await manager.load()
        >>>
        >>> # At the scheduler's session reap point
        >>> await manager.maybe_summarize_completed_sessions(journal)
        >>>
        >>> # Build context for LLM
        >>> context_blocks = await manager.build_context_blocks(current_message)
    """

    def __init__(
        self,
        agent_id: str,
        config: MemoryConfig,
        data_dir: Path | None = None,
        llm_config: LLMConfig | None = None,
    ):
        """Initialize Memory Manager.

        Args:
            agent_id: The agent ID
            config: Memory configuration
            data_dir: The agent's home directory (``{home}/{agent_id}``);
                the memory dir resolves as ``{data_dir}/memory``. When
                None, the global ``paths`` singleton is used — the
                registry always passes an explicit data_dir (unified
                assembly, ADR 0006), so the singleton is only a fallback.
            llm_config: Optional LLM config for summarization
        """
        self.agent_id = agent_id
        self.config = config

        if data_dir is not None:
            self.storage_path = data_dir / "memory"
            self.storage_path.mkdir(parents=True, exist_ok=True)
            self.config_file = self.storage_path / "config.json"
        else:
            self.storage_path = paths.agent_memory_dir(agent_id)
            self.config_file = paths.memory_config_file(agent_id)

        self.episodic = EpisodicMemoryStore(
            agent_id=agent_id,
            storage_path=self.storage_path,
            max_summaries=config.episodic_max_summaries,
            max_facts=config.episodic_max_facts,
        )

        self._loaded = False

        # Initialize summarizer if LLM config provided
        if llm_config:
            self.summarizer: Summarizer | None = Summarizer(llm_config)
        else:
            self.summarizer = None

    # ===== Lifecycle =====

    async def load(self) -> None:
        """Load memory from disk.

        Called once at Agent startup. Loads config and episodic memory.

        ``memory/config.json`` is the single per-agent tuning surface:
        when it exists it replaces the constructor-provided config (the
        registry passes defaults), so a human-edited file takes effect.
        The episodic store limits are snapshotted at construction,
        so the merged values are re-applied to the store before it
        loads. A missing file is created with the current defaults; a
        corrupt one is logged and re-initialized with defaults.
        """
        if self._loaded:
            return

        logger.info("memory_loading", agent_id=self.agent_id)

        # Load or create config
        if self.config_file.exists():
            try:
                async with aiofiles.open(self.config_file, encoding="utf-8") as f:
                    content = await f.read()
                    data = json.loads(content)
                    loaded_config = MemoryConfig.from_dict(data)
                    loaded_config.agent_id = self.agent_id
                    self.config = loaded_config
                    logger.info("memory_config_loaded", agent_id=self.agent_id)
            except Exception as e:
                logger.error("memory_config_load_failed", error=str(e), exc_info=True)
                self.config.agent_id = self.agent_id
                await self._save_config()
        else:
            # Save initial config
            self.config.agent_id = self.agent_id
            await self._save_config()

        # Store limits snapshot at construction; re-apply the (possibly
        # merged) values before the store loads so a tuned config.json is
        # honored from the first boot.
        self.episodic.max_summaries = self.config.episodic_max_summaries
        self.episodic.max_facts = self.config.episodic_max_facts

        await self.episodic.load()

        self._loaded = True
        logger.info("memory_loaded", agent_id=self.agent_id)

    async def _save_config(self) -> None:
        """Save memory config to disk."""
        try:
            async with aiofiles.open(self.config_file, "w", encoding="utf-8") as f:
                await f.write(json.dumps(self.config.to_dict(), indent=2, ensure_ascii=False))
        except Exception as e:
            logger.error("memory_config_save_failed", error=str(e), exc_info=True)

    # ===== Batch summarization (journal -> episodic, ADR 0009) =====

    async def maybe_summarize_completed_sessions(self, logs: SessionLogReader) -> str | None:
        """Summarize completed sessions not yet covered by any Summary.

        Called by the scheduler at its session reap point. The pending
        set is ``logs.list_completed()`` minus every session id recorded
        as ``source_session_ids`` in the recent summaries (scan capped at
        ``episodic_max_summaries`` entries); when the pending count
        reaches ``config.summary_window`` the whole batch is summarized
        from the sessions' journals into one episodic Summary.

        Returns:
            The summary ID if one was generated, None otherwise
        """
        if not self._loaded:
            await self.load()

        completed = await logs.list_completed()
        if not completed:
            return None

        recent = await self.episodic.get_recent_summaries(self.config.episodic_max_summaries)
        summarized = {
            session_id
            for summary in recent
            for session_id in summary.get("source_session_ids", [])
        }
        pending = [sid for sid in completed if sid not in summarized]

        if len(pending) < self.config.summary_window:
            logger.debug(
                "summary_window_not_reached",
                pending_count=len(pending),
                window=self.config.summary_window,
            )
            return None

        projections = [
            self._build_projection(session_id, await logs.read_events(session_id))
            for session_id in pending
        ]
        return await self._summarize_sessions(projections)

    @staticmethod
    def _build_projection(session_id: str, events: list[dict[str, Any]]) -> SessionProjection:
        """Rebuild one session's summarization view from its journal events.

        The projection is: the real trigger content (from
        ``session/start``), every round's LLM response content, and a
        one-line digest per tool result.
        """
        trigger_content = ""
        llm_responses: list[str] = []
        tool_results: list[str] = []
        for event in events:
            event_type = event.get("type")
            if event_type == "session/start":
                trigger_content = str(event.get("trigger_content") or "")
            elif event_type == "llm/response":
                content = (event.get("response") or {}).get("content") or ""
                if content:
                    llm_responses.append(str(content))
            elif event_type == "tool/result":
                tool_call = event.get("tool_call") or {}
                name = (tool_call.get("function") or {}).get("name", "unknown")
                result = event.get("result") or {}
                text = (
                    result.get("message")
                    or result.get("error")
                    or json.dumps(result.get("data") or {}, ensure_ascii=False)
                )
                tool_results.append(f"{name}: {text}")
        return SessionProjection(
            session_id=session_id,
            trigger_content=trigger_content,
            llm_responses=llm_responses,
            tool_results=tool_results,
        )

    async def _summarize_sessions(self, projections: list[SessionProjection]) -> str | None:
        """Summarize a batch of session projections into episodic memory."""
        source_session_ids = [p.session_id for p in projections]
        turn_count = sum(len(p.llm_responses) for p in projections)
        logger.info(
            "summary_generation_started",
            session_count=len(projections),
            turn_count=turn_count,
        )

        summary_id: str | None = None
        if self.summarizer:
            try:
                existing_facts = await self.episodic.get_recent_facts(20)
                summary_dict, facts = await self.summarizer.process_sessions(
                    sessions=projections,
                    existing_facts=existing_facts,
                )

                if summary_dict:
                    summary = Summary(
                        timestamp=summary_dict["timestamp"],
                        summary_text=summary_dict["summary_text"],
                        source_session_ids=source_session_ids,
                        interaction_count=summary_dict.get("interaction_count", turn_count),
                        key_decisions=summary_dict.get("key_decisions", []),
                        action_items=summary_dict.get("action_items", []),
                    )
                    summary_id = await self.episodic.add_summary(summary.to_dict())

                    for fact_data in facts:
                        fact_obj = Fact(
                            timestamp=datetime.now().isoformat(),
                            session_id="",
                            fact_type=FactType(fact_data.get("fact_type", "GENERAL")),
                            subject=fact_data.get("subject", "用户"),
                            predicate=fact_data.get("predicate", "提到"),
                            object=fact_data.get("object", ""),
                            description=fact_data.get("description", ""),
                            confidence=fact_data.get("confidence", 0.5),
                        )
                        await self.episodic.add_fact(fact_obj.to_dict())

                    logger.info(
                        "summary_generated",
                        summary_id=summary_id,
                        fact_count=len(facts),
                    )
                else:
                    summary_id = await self._persist_fallback_summary(projections)

            except Exception as e:
                logger.error("llm_summarization_failed", error=str(e), exc_info=True)
                summary_id = await self._persist_fallback_summary(projections)
        else:
            summary_id = await self._persist_fallback_summary(projections)

        return summary_id

    async def _persist_fallback_summary(
        self, projections: list[SessionProjection]
    ) -> str | None:
        """Persist a mechanical fallback summary without LLM.

        Runs inside ``_summarize_sessions``'s fallback paths; a
        persistence failure here must not escape into the scheduler's
        reap path.
        """
        summary = Summary(
            timestamp=datetime.now().isoformat(),
            summary_text=Summarizer._generate_fallback_summary(projections),
            source_session_ids=[p.session_id for p in projections],
            interaction_count=sum(len(p.llm_responses) for p in projections),
        )
        try:
            summary_id = await self.episodic.add_summary(summary.to_dict())
        except Exception as e:
            logger.error("summary_persist_failed", error=str(e), exc_info=True)
            return None
        logger.info("simple_summary_generated", summary_id=summary_id)
        return summary_id

    async def build_context_blocks(
        self,
        current_message: Any | None = None,
    ) -> list[ContextBlock]:
        """Build context blocks for LLM context injection.

        Assembles relevant memory into formatted blocks:
        - Episodic summaries (recent 5)
        - Key facts (recent 10)

        Args:
            current_message: The current message being processed

        Returns:
            List of ContextBlock objects ordered by priority
        """
        if not self._loaded:
            await self.load()

        blocks: list[ContextBlock] = []

        # Get recent summaries
        summaries = await self.episodic.get_recent_summaries(self.config.context_max_summaries)
        if summaries:
            summary_lines = []
            summary_ids: list[str] = []
            for s in summaries:
                text = s.get("summary_text", "")
                timestamp = s.get("timestamp", "")[:10]  # Just date
                if text:
                    summary_lines.append(f"- [{timestamp}] {text}")
                    if (sid := s.get("id")) is not None:
                        summary_ids.append(sid)

            if summary_lines:
                blocks.append(
                    ContextBlock(
                        block_type="episodic_summaries",
                        priority=1,
                        content="\n".join(summary_lines),
                        source_ids=summary_ids,
                    )
                )

        # Get recent facts
        facts = await self.episodic.get_recent_facts(self.config.context_max_facts)
        if facts:
            fact_lines = []
            fact_ids: list[str] = []
            for f in facts:
                desc = f.get("description", "")
                if not desc:
                    # Build from subject-predicate-object
                    desc = f"{f.get('subject', '')} {f.get('predicate', '')} {f.get('object', '')}"
                if desc.strip():
                    fact_lines.append(f"- {desc.strip()}")
                    if (fid := f.get("id")) is not None:
                        fact_ids.append(fid)

            if fact_lines:
                blocks.append(
                    ContextBlock(
                        block_type="key_facts",
                        priority=2,
                        content="\n".join(fact_lines),
                        source_ids=fact_ids,
                    )
                )

        # Sort by priority
        blocks.sort(key=lambda b: b.priority)

        return blocks

    # ===== LLM Tool Operations =====

    async def remember_fact(
        self,
        fact: str,
        fact_type: str = "GENERAL",
        subject: str = "用户",
        predicate: str = "提到",
        object: str = "",
        importance: float = 0.5,
    ) -> str:
        """Record a fact (called by LLM via tool).

        Args:
            fact: Natural language description of the fact
            fact_type: Type of fact (USER_PREFERENCE, PROJECT_STATE, etc.)
            subject: Subject of the fact
            predicate: Predicate/relationship
            object: Object of the fact
            importance: Importance score (0-1)

        Returns:
            The fact ID
        """
        if not self._loaded:
            await self.load()

        try:
            fact_type_enum = FactType(fact_type)
        except ValueError:
            fact_type_enum = FactType.GENERAL

        fact_obj = Fact(
            timestamp=datetime.now().isoformat(),
            session_id="",
            fact_type=fact_type_enum,
            subject=subject,
            predicate=predicate,
            object=object or fact[:50],  # Use fact text as object if not provided
            description=fact,
            confidence=importance,
        )

        fact_id = await self.episodic.add_fact(fact_obj.to_dict())
        logger.info("fact_recorded", fact_id=fact_id, content=fact)

        return fact_id

    async def search_memories(
        self,
        query: str,
        limit: int = 5,
    ) -> str:
        """Search memories (called by LLM via tool).

        Simple keyword-based search across summaries and facts.

        Args:
            query: Search query string
            limit: Maximum results to return

        Returns:
            Formatted search results as text
        """
        if not self._loaded:
            await self.load()

        # Simple keyword extraction (split by spaces)
        keywords = [kw.strip() for kw in query.split() if len(kw.strip()) > 1]
        if not keywords:
            keywords = [query]

        # Search summaries
        summary_results = await self.episodic.search_summaries_by_keyword(keywords, limit)

        # Search facts
        fact_results = await self.episodic.search_facts_by_keyword(keywords, limit)

        # Format results
        lines = [f"搜索结果 for '{query}':", ""]

        if summary_results:
            lines.append("## 相关会话摘要")
            for s in summary_results:
                text = s.get("summary_text", "")
                lines.append(f"- {text}")
            lines.append("")

        if fact_results:
            lines.append("## 相关事实")
            for f in fact_results:
                desc = f.get("description", "")
                if not desc:
                    desc = f"{f.get('subject', '')} {f.get('predicate', '')} {f.get('object', '')}"
                lines.append(f"- {desc}")
            lines.append("")

        if not summary_results and not fact_results:
            lines.append("未找到相关记忆。")

        return "\n".join(lines)

    async def forget(
        self,
        query: str,
        confirm: bool = False,
    ) -> str:
        """Forget/delete memories (called by LLM or user).

        Args:
            query: Description of what to forget
            confirm: If True, actually delete; if False, just preview

        Returns:
            Result message
        """
        if not self._loaded:
            await self.load()

        # Search for matching items
        keywords = [kw.strip() for kw in query.split() if len(kw.strip()) > 1]
        if not keywords:
            keywords = [query]

        summary_matches = await self.episodic.search_summaries_by_keyword(keywords, 100)
        fact_matches = await self.episodic.search_facts_by_keyword(keywords, 100)

        total_matches = len(summary_matches) + len(fact_matches)

        if total_matches == 0:
            return f"未找到匹配 '{query}' 的记忆。"

        if not confirm:
            # Preview mode
            lines = [f"找到 {total_matches} 条匹配记忆:", ""]
            if summary_matches:
                lines.append(f"摘要: {len(summary_matches)} 条")
            if fact_matches:
                lines.append(f"事实: {len(fact_matches)} 条")
            lines.append("")
            lines.append("调用 forget(query='...', confirm=True) 确认删除。")
            return "\n".join(lines)

        # Delete mode
        deleted_count = 0
        for s in summary_matches:
            if (sid := s.get("id")) is not None and await self.episodic.delete_summary(sid):
                deleted_count += 1

        for f in fact_matches:
            if (fid := f.get("id")) is not None and await self.episodic.delete_fact(fid):
                deleted_count += 1

        return f"已删除 {deleted_count} 条记忆。"

    async def forget_fact(self, fact_id: str) -> bool:
        """Delete one fact by ID (called by the reflection engine).

        The reflection prompt lists facts by an ID prefix, so an exact
        miss falls back to unique-prefix resolution. Unknown or ambiguous
        IDs delete nothing and are logged — never a silent no-op.

        Args:
            fact_id: The fact's full ID or an ID prefix

        Returns:
            True if exactly one fact was deleted
        """
        if not self._loaded:
            await self.load()

        if await self.episodic.delete_fact(fact_id):
            logger.info("fact_deleted", agent_id=self.agent_id, fact_id=fact_id)
            return True

        all_facts = await self.episodic.get_all_facts(include_superseded=True)
        matches = [f for f in all_facts if str(f.get("id", "")).startswith(fact_id)]
        if len(matches) == 1:
            resolved_id = str(matches[0]["id"])
            await self.episodic.delete_fact(resolved_id)
            logger.info(
                "fact_deleted",
                agent_id=self.agent_id,
                fact_id=resolved_id,
                requested_id=fact_id,
            )
            return True

        if matches:
            logger.warning(
                "fact_delete_ambiguous",
                agent_id=self.agent_id,
                fact_id=fact_id,
                match_count=len(matches),
            )
        else:
            logger.warning(
                "fact_delete_not_found", agent_id=self.agent_id, fact_id=fact_id
            )
        return False

    async def get_memory_digest(self, logs: SessionLogReader) -> dict[str, Any]:
        """Get a digest of current memory state for reflection/audit.

        Returns a dict with summaries, facts, and the number of completed
        sessions not yet covered by a Summary for the reflection
        sub-agent to analyze.

        Returns:
            Dict with keys: unsummarized_session_count, summary_count,
            fact_count, summaries, facts
        """
        if not self._loaded:
            await self.load()

        summaries = await self.episodic.get_recent_summaries(20)
        facts = await self.episodic.get_recent_facts(50)

        completed = await logs.list_completed()
        summarized = {
            session_id
            for summary in summaries
            for session_id in summary.get("source_session_ids", [])
        }
        unsummarized_count = sum(1 for sid in completed if sid not in summarized)

        return {
            "unsummarized_session_count": unsummarized_count,
            "summary_count": len(summaries),
            "fact_count": len(facts),
            "summaries": summaries,
            "facts": facts,
        }
