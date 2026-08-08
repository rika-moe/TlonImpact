"""Memory Manager - Coordinates Working and Episodic memory stores."""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast

import aiofiles

from tlon.base.config import LLMConfig
from tlon.base.logger import get_logger
from tlon.base.paths import paths

from .episodic_store import EpisodicMemoryStore
from .models import (
    ContextBlock,
    Fact,
    FactType,
    Interaction,
    MemoryConfig,
    Summary,
)
from .summarizer import Summarizer
from .working_store import WorkingMemoryStore

logger = get_logger(__name__)


class MemoryManager:
    """Main interface for Agent memory management.

    Coordinates Working Memory (recent interactions) and Episodic Memory
    (summaries and facts) to provide a unified memory interface.

    Usage:
        >>> manager = MemoryManager("dev-001", config, Path("./data"))
        >>> await manager.load()
        >>>
        >>> # After interaction completes
        >>> await manager.on_interaction_complete(message, response, tool_results)
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
            data_dir: Ignored (for backward compat). Uses TlonPaths internally.
            llm_config: Optional LLM config for summarization
        """
        self.agent_id = agent_id
        self.config = config

        self.storage_path = paths.agent_memory_dir(agent_id)

        # Config file path
        self.config_file = paths.memory_config_file(agent_id)

        # Initialize stores
        self.working = WorkingMemoryStore(
            agent_id=agent_id,
            storage_path=self.storage_path,
            max_items=config.working_max_items,
        )
        self.episodic = EpisodicMemoryStore(
            agent_id=agent_id,
            storage_path=self.storage_path,
            max_summaries=config.episodic_max_summaries,
            max_facts=config.episodic_max_facts,
        )

        self._loaded = False
        self._session_id: str | None = None
        self._last_interaction_time: datetime | None = None

        # Initialize summarizer if LLM config provided
        if llm_config:
            self.summarizer: Summarizer | None = Summarizer(llm_config)
        else:
            self.summarizer = None

    # ===== Lifecycle =====

    async def load(self) -> None:
        """Load memory from disk.

        Called once at Agent startup. Loads config, working memory,
        and episodic memory.
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
                    # Merge with provided config (provided takes precedence)
                    if not self.config.agent_id:
                        self.config = loaded_config
                    logger.info("memory_config_loaded", agent_id=self.agent_id)
            except Exception as e:
                logger.error("memory_config_load_failed", error=str(e), exc_info=True)
                await self._save_config()
        else:
            # Save initial config
            self.config.agent_id = self.agent_id
            await self._save_config()

        # Load working memory
        await self.working.load()

        # Load episodic memory
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

    async def save(self) -> None:
        """Explicitly save all memory to disk.

        Note: Memory is persisted incrementally, so this is usually
        not necessary. Useful for graceful shutdown.
        """
        await self._save_config()
        # Stores persist automatically on add operations
        logger.info("memory_saved", agent_id=self.agent_id)

    def set_session_id(self, session_id: str) -> None:
        """Set the current session ID.

        Args:
            session_id: Unique identifier for this session
        """
        self._session_id = session_id

    # ===== System Auto-Triggered Operations =====

    async def add_interaction(
        self,
        message: Any,
        response: Any,
        tool_results: list[Any] | None = None,
    ) -> str:
        """Store a completed interaction.

        Automatically called after each Agent response. Stores the
        interaction in Working Memory and checks if summary should
        be generated.

        Args:
            message: The incoming message (Message object or dict)
            response: The agent's response (response object or dict)
            tool_results: Optional list of tool execution results

        Returns:
            The interaction ID
        """
        # Also expose as on_interaction_complete for backward compatibility
        return await self._store_interaction(message, response, tool_results)

    async def on_interaction_complete(
        self,
        message: Any,
        response: Any,
        tool_results: list[Any] | None = None,
    ) -> str:
        """Alias for add_interaction - called by Agent after each response.

        Args:
            message: The incoming message (Message object or dict)
            response: The agent's response (response object or dict)
            tool_results: Optional list of tool execution results

        Returns:
            The interaction ID
        """
        return await self._store_interaction(message, response, tool_results)

    async def _store_interaction(
        self,
        message: Any,
        response: Any,
        tool_results: list[Any] | None = None,
    ) -> str:
        """Internal method to store an interaction.

        Args:
            message: The incoming message (Message object or dict)
            response: The agent's response (response object or dict)
            tool_results: Optional list of tool execution results

        Returns:
            The interaction ID
        """
        if not self._loaded:
            await self.load()

        # Ensure session ID
        if not self._session_id:
            self._session_id = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Convert inputs to serializable format
        msg_dict = self._convert_to_dict(message)
        resp_dict = self._convert_response_to_dict(response)
        tool_dicts = [self._convert_to_dict(tr) for tr in (tool_results or [])]

        # Extract tool calls from response if present
        tool_calls = []
        if isinstance(response, dict) and "tool_calls" in response:
            tool_calls = response["tool_calls"]
        elif hasattr(response, "tool_calls") and response.tool_calls:
            tool_calls = [
                tc.to_dict() if hasattr(tc, "to_dict") else tc for tc in response.tool_calls
            ]

        # Create interaction
        interaction = Interaction(
            timestamp=datetime.now().isoformat(),
            session_id=self._session_id,
            message=msg_dict,
            response=resp_dict,
            tool_calls=tool_calls,
            tool_results=tool_dicts,
        )

        # Store in working memory
        interaction_id = await self.working.add(interaction.to_dict())

        # Update last interaction time
        self._last_interaction_time = datetime.now()

        logger.debug("interaction_stored", interaction_id=interaction_id)

        # Check if we should generate summary
        await self._check_summary_trigger()

        return interaction_id

    def _convert_to_dict(self, obj: Any) -> dict[str, Any]:
        """Convert object to dictionary."""
        if obj is None:
            return {}
        if hasattr(obj, "to_dict"):
            return cast(dict[str, Any], obj.to_dict())
        if hasattr(obj, "__dict__"):
            return {k: v for k, v in obj.__dict__.items() if not k.startswith("_")}
        if isinstance(obj, dict):
            return obj
        return {"content": str(obj)}

    def _convert_response_to_dict(self, response: Any) -> dict[str, Any] | None:
        """Convert response object to dictionary."""
        if response is None:
            return None
        if hasattr(response, "to_dict"):
            return cast(dict[str, Any], response.to_dict())
        if isinstance(response, dict):
            return response
        if hasattr(response, "content"):
            result = {"content": response.content}
            if hasattr(response, "finish_reason"):
                result["finish_reason"] = response.finish_reason
            return result
        return {"content": str(response)}

    async def _check_summary_trigger(self) -> None:
        """Check if summary should be generated.

        Triggers:
        1. Working memory is full (reaches max_items)
        2. Session has been idle for configured timeout
        """
        # Check if working memory is full
        if await self.working.is_full():
            logger.info("working_memory_full")
            await self.generate_summary()

    async def check_idle_timeout(self) -> str | None:
        """Check if idle timeout has been reached and generate summary.

        This should be called periodically (e.g., before processing
        a new message) to check if the session has been idle.

        Returns:
            Summary ID if generated, None otherwise
        """
        if not self._loaded:
            return None

        if not self._last_interaction_time:
            return None

        # Check if there's anything to summarize
        count = await self.working.count()
        if count == 0:
            return None

        # Calculate idle time
        idle_time = datetime.now() - self._last_interaction_time
        timeout = timedelta(minutes=self.config.idle_timeout_minutes)

        if idle_time >= timeout:
            logger.info(
                "idle_timeout",
                idle_minutes=round(idle_time.total_seconds() / 60, 1),
            )
            return await self.generate_summary(force=True)

        return None

    async def build_context_blocks(
        self,
        current_message: Any | None = None,
    ) -> list[ContextBlock]:
        """Build context blocks for LLM context injection.

        Assembles relevant memory into formatted blocks:
        - Episodic summaries (recent 5)
        - Key facts (recent 10)
        - Working memory interactions (recent 5)

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

        # Get recent working memory interactions
        interactions = await self.working.get_recent(self.config.context_max_working)
        if interactions:
            interaction_lines = []
            interaction_ids: list[str] = []
            for i, inter in enumerate(interactions, 1):
                msg = inter.get("message", {})
                resp = inter.get("response", {})
                tool_results = inter.get("tool_results", [])

                # Format: [User] message
                sender = msg.get("sender_name", "User")
                content = msg.get("content", "")
                lines = [f"{i}. [{sender}] {content}"]

                # Add tool results if present
                if tool_results:
                    for tr in tool_results:
                        stdout = tr.get("stdout", "")
                        if stdout:
                            lines.append(f"   [Tool Result] {stdout[:200]}")  # Truncate

                # Add response
                resp_content = ""
                if isinstance(resp, dict):
                    resp_content = resp.get("content", "")
                elif isinstance(resp, str):
                    resp_content = resp

                if resp_content:
                    lines.append(f"   [Agent] {resp_content[:200]}")  # Truncate

                interaction_lines.append("\n".join(lines))
                if (iid := inter.get("id")) is not None:
                    interaction_ids.append(iid)

            if interaction_lines:
                blocks.append(
                    ContextBlock(
                        block_type="recent_working",
                        priority=3,
                        content="\n\n".join(interaction_lines),
                        source_ids=interaction_ids,
                    )
                )

        # Sort by priority
        blocks.sort(key=lambda b: b.priority)

        return blocks

    async def generate_summary(
        self, force: bool = False, instructions: str | None = None
    ) -> str | None:
        """Generate a session summary from working memory.

        Uses snapshot + delta-clear: snapshots working memory before the
        LLM call, and only removes snapshot items after. Items added to
        working memory during the LLM call are preserved.

        Args:
            force: If True, generate summary even if not enough interactions
            instructions: Optional focus for the summary, passed to the
                summarizer so specific content is preserved

        Returns:
            The summary ID if generated, None if no interactions
        """
        if not self._loaded:
            await self.load()

        # ── 1. Snapshot working memory (atomically, inside store lock) ──
        interactions = await self.working.get_all()
        if not interactions:
            logger.debug("no_interactions_to_summarize")
            return None

        if not force and len(interactions) < self.config.working_summary_window:
            logger.debug(
                "insufficient_interactions",
                interaction_count=len(interactions),
                window=self.config.working_summary_window,
            )
            return None

        snapshot_ids = {i["id"] for i in interactions}
        source_ids = list(snapshot_ids)
        count = len(interactions)
        logger.info("summary_generation_started", interaction_count=count)

        # ── 2. LLM call (no locks held, can run long) ──
        summary_id: str | None = None
        if self.summarizer:
            try:
                existing_facts = await self.episodic.get_recent_facts(20)
                summary_dict, facts = await self.summarizer.process_interactions(
                    interactions=interactions,
                    session_id=self._session_id or "unknown",
                    existing_facts=existing_facts,
                    instructions=instructions,
                )

                if summary_dict:
                    summary_dict["id"] = Summary(
                        timestamp="", session_id="", summary_text=""
                    ).id
                    summary_dict["source_interactions"] = source_ids
                    summary = Summary.from_dict(summary_dict)
                    summary_id = await self.episodic.add_summary(summary.to_dict())

                    for fact_data in facts:
                        fact_obj = Fact(
                            timestamp=datetime.now().isoformat(),
                            session_id=self._session_id or "unknown",
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
                    summary_id = await self._generate_simple_summary(interactions, source_ids)

            except Exception as e:
                logger.error("llm_summarization_failed", error=str(e), exc_info=True)
                summary_id = await self._generate_simple_summary(interactions, source_ids)
        else:
            summary_id = await self._generate_simple_summary(interactions, source_ids)

        # ── 3. Delta-clear: only remove snapshot items ──
        removed = await self.working.remove_by_ids(snapshot_ids)
        logger.info(
            "working_memory_delta_cleared",
            removed_count=removed,
            total_count=count,
        )

        return summary_id

    async def _generate_simple_summary(
        self, interactions: list[dict[str, Any]], source_ids: list[str]
    ) -> str:
        """Generate a simple fallback summary without LLM.

        Delegates the summary text to Summarizer._generate_fallback_summary
        to keep a single fallback implementation.
        """
        count = len(interactions)
        summary_text = Summarizer._generate_fallback_summary(interactions)

        summary = Summary(
            timestamp=datetime.now().isoformat(),
            session_id=self._session_id or "unknown",
            summary_text=summary_text,
            source_interactions=source_ids,
            interaction_count=count,
        )

        summary_id = await self.episodic.add_summary(summary.to_dict())
        logger.info("simple_summary_generated", summary_id=summary_id)

        return summary_id

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

        # Ensure session ID
        if not self._session_id:
            self._session_id = datetime.now().strftime("%Y%m%d_%H%M%S")

        fact_obj = Fact(
            timestamp=datetime.now().isoformat(),
            session_id=self._session_id,
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

    async def get_memory_digest(self) -> dict[str, Any]:
        """Get a digest of current memory state for reflection/audit.

        Returns a dict with summaries, facts, and working memory count
        for the reflection sub-agent to analyze.

        Returns:
            Dict with keys: working_count, summary_count, fact_count,
            summaries, facts, last_interaction_time
        """
        if not self._loaded:
            await self.load()

        summaries = await self.episodic.get_recent_summaries(20)
        facts = await self.episodic.get_recent_facts(50)
        working_count = await self.working.count()

        return {
            "working_count": working_count,
            "summary_count": len(summaries),
            "fact_count": len(facts),
            "summaries": summaries,
            "facts": facts,
            "last_interaction_time": (
                self._last_interaction_time.isoformat()
                if self._last_interaction_time
                else None
            ),
        }

    # ===== User Command Operations =====

    async def get_history_text(self, limit: int = 20) -> str:
        """Get formatted history for user display.

        Args:
            limit: Number of interactions to show

        Returns:
            Formatted history text
        """
        if not self._loaded:
            await self.load()

        interactions = await self.working.get_recent(limit)

        if not interactions:
            return "没有历史记录。"

        lines = ["## 历史记录", ""]

        for _i, inter in enumerate(reversed(interactions), 1):
            msg = inter.get("message", {})
            resp = inter.get("response", {})

            sender = msg.get("sender_name", "User")
            content = msg.get("content", "")
            timestamp = inter.get("timestamp", "")[11:19]  # Just time

            lines.append(f"[{timestamp}] {sender}: {content}")

            if resp:
                resp_content = ""
                if isinstance(resp, dict):
                    resp_content = resp.get("content", "")
                elif isinstance(resp, str):
                    resp_content = resp
                if resp_content:
                    lines.append(f"  → Agent: {resp_content[:200]}")

            lines.append("")

        return "\n".join(lines)

    async def get_summary_text(self) -> str:
        """Get formatted summaries for user display.

        Returns:
            Formatted summaries text
        """
        if not self._loaded:
            await self.load()

        summaries = await self.episodic.get_recent_summaries(10)

        if not summaries:
            return "没有会话摘要。"

        lines = ["## 会话摘要", ""]

        for s in summaries:
            text = s.get("summary_text", "")
            timestamp = s.get("timestamp", "")[:16]  # Date + time
            count = s.get("interaction_count", 0)

            lines.append(f"[{timestamp}] ({count} 次交互)")
            lines.append(f"  {text}")
            lines.append("")

        return "\n".join(lines)

    async def get_facts_text(self) -> str:
        """Get formatted facts for user display.

        Returns:
            Formatted facts text
        """
        if not self._loaded:
            await self.load()

        facts = await self.episodic.get_recent_facts(50)

        if not facts:
            return "没有记录的事实。"

        lines = ["## 已知事实", ""]

        for f in facts:
            desc = f.get("description", "")
            if not desc:
                desc = f"{f.get('subject', '')} {f.get('predicate', '')} {f.get('object', '')}"

            fact_type = f.get("fact_type", "GENERAL")
            lines.append(f"[{fact_type}] {desc}")

        return "\n".join(lines)
