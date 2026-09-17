"""Memory Manager - the claim-based memory interface (ADR 0017).

The session journal (ADR 0007) is the sole interaction truth; completed
sessions are distilled in batches into claims — natural-language
assertions with provenance — stored append-only in ``claims.jsonl``.
Distillation progress is a scalar timestamp watermark persisted in
``distillation.json`` (session ids are uuids without temporal order, so
no id-based high-water mark is possible). Retrieval is JIT: the current
mail embeds into a query against a sqlite-vec index (``claims_vec.db``,
a pure derived view of the claim store) and active claims are ranked by
a composite similarity/recency/importance score; without an embedding
model or on any index failure it degrades to recency order. The manager
keeps the claim store, the index, the context-block assembly for the
``(knowledge)`` section, and the batch distillation trigger the
scheduler calls at its session reap point.
"""

import asyncio
import contextlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

import aiofiles

from tlon.base.config import LLMConfig
from tlon.base.embedding_client import EmbeddingClient
from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths, paths

from .claim_store import ClaimStore
from .distiller import Distiller
from .models import Claim, ContextBlock, MemoryConfig, SessionProjection
from .vector_index import VectorIndex

logger = get_logger(__name__)

#: JIT retrieval composite score (ADR 0017 stage 2, Generative Agents
#: shape): ``score = α·similarity + β·recency + γ·importance``.
#: Similarity dominates — relevance to the current mail is the whole
#: point of JIT injection (); importance lets critical claims win
#: near-ties; recency is deliberately the weakest factor because the
#: recency-ordered fallback already owns "latest first" and an old but
#: relevant claim must not drown under fresh noise.
_SCORE_ALPHA_SIMILARITY = 1.0
_SCORE_BETA_RECENCY = 0.3
_SCORE_GAMMA_IMPORTANCE = 0.5
#: Recency decays exponentially with this half-life in days (the
#: Generative Agents decay, re-parameterized as a half-life).
_RECENCY_HALF_LIFE_DAYS = 7.0
#: The KNN query pulls a pool larger than k so the composite score can
#: re-rank candidates pure similarity order would miss.
_RETRIEVAL_POOL_FACTOR = 4


class Embedder(Protocol):
    """The embedding surface the retrieval layer needs.

    Injected structurally so tests never touch the network — in
    production an ``EmbeddingClient`` built from the member's
    ``llm.embedding_model_name``.
    """

    @property
    def model_name(self) -> str:
        """The embedding model identity recorded in the index metadata."""
        ...

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """One embedding vector per input text, in order."""
        ...


class SessionLogReader(Protocol):
    """The journal read surface the distillation trigger needs.

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

    Owns the claim store, builds the context blocks injected into the
    LLM conversation, and runs the batch distillation trigger
    (journal -> claims).

    Usage:
        >>> manager = MemoryManager("dev-001", config, Path("./data"))
        >>> await manager.load()
        >>>
        >>> # At the scheduler's session reap point
        >>> await manager.maybe_distill_completed_sessions(journal)
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
        tlon_paths: TlonPaths | None = None,
        embedder: Embedder | None = None,
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
            llm_config: Optional LLM config for distillation; its
                ``embedding_model_name`` (when set) also builds the
                embedding client for vector retrieval
            tlon_paths: Path resolver handed to the distiller's LLM
                client for ledger attribution (issue ); defaults to
                the global ``~/.tlon`` layout.
            embedder: Embedding client override (tests inject a fake);
                None = build from ``llm_config.embedding_model_name``,
                and when that is unset too, no vector retrieval at all
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

        self.distillation_file = self.storage_path / "distillation.json"

        self.claims = ClaimStore(agent_id=agent_id, storage_path=self.storage_path)

        self._loaded = False
        self._watermark: str | None = None

        # Initialize distiller if LLM config provided
        if llm_config:
            self.distiller: Distiller | None = Distiller(
                llm_config, agent_id=agent_id, tlon_paths=tlon_paths
            )
        else:
            self.distiller = None

        # Vector retrieval (ADR 0017 stage 2): an embedder plus a derived
        # sqlite-vec index. Without an embedding model configured the
        # manager degrades to recency-ordered retrieval — a first-class
        # mode, not an error (some providers expose no embedding endpoint).
        if embedder is not None:
            self._embedder: Embedder | None = embedder
        elif llm_config is not None and llm_config.embedding_model_name:
            self._embedder = EmbeddingClient(llm_config)
        else:
            self._embedder = None
        self._index: VectorIndex | None = None
        self._index_lock = asyncio.Lock()

    # ===== Lifecycle =====

    async def load(self) -> None:
        """Load memory from disk.

        Called once at Agent startup. Loads config, claims, and the
        distillation watermark.

        ``memory/config.json`` is the single per-agent tuning surface:
        when it exists it replaces the constructor-provided config (the
        registry passes defaults), so a human-edited file takes effect.
        A missing file is created with the current defaults; a corrupt
        one is logged and re-initialized with defaults.
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

        self._watermark = self._load_watermark()
        await self.claims.load()
        await self._init_vector_index()

        self._loaded = True
        logger.info("memory_loaded", agent_id=self.agent_id)

    # ===== Vector index (derived view of claims.jsonl, ADR 0017 stage 2) =====

    async def _init_vector_index(self) -> None:
        """Open the vector index; rebuild or backfill against the claims.

        The index records its embedding model + dimension; a mismatch
        against the configured model (embedding spaces are incompatible
        across models) triggers a full rebuild from the active claims —
        the index is a pure derived view, so rebuilding is N embedding
        calls, never a migration. Any failure (provider down, no API
        key) degrades to no index: retrieval falls back to recency order
        and the next startup retries.
        """
        self._index = None
        if self._embedder is None:
            return
        try:
            index = VectorIndex(self.storage_path / "claims_vec.db")
        except Exception as e:
            logger.warning("vector_index_open_failed", error=str(e))
            return
        self._index = index

        embedder = self._embedder
        active = await self.claims.get_all_claims()
        meta = index.model_meta()
        if meta is not None and meta[0] == embedder.model_name:
            await self._backfill_index(index, embedder, active)
            return
        if meta is not None:
            logger.info(
                "vector_index_model_changed", old_model=meta[0], new_model=embedder.model_name
            )
        try:
            vectors = await embedder.embed([str(c.get("text", "")) for c in active])
            index.rebuild(
                embedder.model_name,
                [
                    (str(c["id"]), vector)
                    for c, vector in zip(active, vectors, strict=True)
                ],
            )
            logger.info("vector_index_rebuilt", claim_count=len(active))
        except Exception as e:
            logger.warning("vector_index_rebuild_failed", error=str(e))
            index.close()
            self._index = None

    async def _backfill_index(
        self, index: VectorIndex, embedder: Embedder, active: list[dict[str, Any]]
    ) -> None:
        """Index active claims missing vectors; prune rows of dead claims.

        Covers claims written while the index was unavailable (embedding
        outage) and rows orphaned by out-of-band claim edits — both
        directions of drift between the store and its derived view.
        """
        active_ids = {str(c.get("id")) for c in active}
        indexed = index.indexed_ids()
        for orphan in indexed - active_ids:
            index.delete(orphan)
        missing = [c for c in active if str(c.get("id")) not in indexed]
        if not missing:
            return
        try:
            vectors = await embedder.embed([str(c.get("text", "")) for c in missing])
            for c, vector in zip(missing, vectors, strict=True):
                index.upsert(str(c["id"]), vector)
            logger.info("vector_index_backfilled", claim_count=len(missing))
        except Exception as e:
            logger.warning("vector_index_backfill_failed", error=str(e))

    async def _index_claims(self, claims: list[dict[str, Any]]) -> None:
        """Best-effort: embed freshly written claims into the index.

        Failures only log — an unindexed claim stays reachable via the
        recency fallback, and the next startup's backfill retries it.
        """
        if self._index is None or self._embedder is None or not claims:
            return
        async with self._index_lock:
            try:
                vectors = await self._embedder.embed([str(c.get("text", "")) for c in claims])
                entries = [
                    (str(c["id"]), vector)
                    for c, vector in zip(claims, vectors, strict=True)
                    if c.get("id") is not None
                ]
                if self._index.model_meta() is None:
                    # Empty memory: the first write bootstraps the table
                    # (dimension is only known once vectors exist).
                    self._index.rebuild(self._embedder.model_name, entries)
                else:
                    for claim_id, vector in entries:
                        self._index.upsert(claim_id, vector)
            except Exception as e:
                logger.warning("vector_index_upsert_failed", error=str(e))

    async def _save_config(self) -> None:
        """Save memory config to disk."""
        try:
            async with aiofiles.open(self.config_file, "w", encoding="utf-8") as f:
                await f.write(json.dumps(self.config.to_dict(), indent=2, ensure_ascii=False))
        except Exception as e:
            logger.error("memory_config_save_failed", error=str(e), exc_info=True)

    # ===== Distillation watermark =====

    def _load_watermark(self) -> str | None:
        """Read the distillation watermark; a missing/corrupt file reads
        as no progress (everything is pending — the safe direction)."""
        if not self.distillation_file.exists():
            return None
        try:
            data = json.loads(self.distillation_file.read_text(encoding="utf-8"))
            watermark = data.get("last_distilled_at")
            return str(watermark) if watermark else None
        except Exception as e:
            logger.warning("distillation_watermark_load_failed", error=str(e))
            return None

    async def _save_watermark(self, watermark: str) -> None:
        """Persist the watermark atomically (temp file + os.replace)."""
        tmp_file = self.distillation_file.with_name(self.distillation_file.name + ".tmp")
        try:
            async with aiofiles.open(tmp_file, "w", encoding="utf-8") as f:
                await f.write(json.dumps({"last_distilled_at": watermark}, ensure_ascii=False))
            await asyncio.to_thread(os.replace, tmp_file, self.distillation_file)
            self._watermark = watermark
        except Exception as e:
            logger.error("distillation_watermark_save_failed", error=str(e), exc_info=True)
            with contextlib.suppress(OSError):
                tmp_file.unlink(missing_ok=True)

    @staticmethod
    def _session_started_at(events: list[dict[str, Any]]) -> datetime | None:
        """The session's start time from its ``session/start`` event;
        None when the log carries no usable timestamp."""
        for event in events:
            if event.get("type") != "session/start":
                continue
            ts = event.get("ts")
            if not ts:
                return None
            try:
                return datetime.fromisoformat(str(ts))
            except ValueError:
                return None
        return None

    async def _collect_pending(
        self, logs: SessionLogReader
    ) -> list[tuple[str, datetime | None, list[dict[str, Any]]]]:
        """Completed sessions newer than the watermark, with their events.

        The watermark is a scalar timestamp because session ids are
        uuids without temporal order. A session whose start time cannot
        be determined is always pending — never silently skipped.
        """
        watermark: datetime | None = None
        if self._watermark:
            try:
                watermark = datetime.fromisoformat(self._watermark)
            except ValueError:
                logger.warning("distillation_watermark_invalid", watermark=self._watermark)

        pending = []
        for session_id in await logs.list_completed():
            events = await logs.read_events(session_id)
            started_at = self._session_started_at(events)
            if started_at is None or watermark is None or started_at > watermark:
                pending.append((session_id, started_at, events))
        return pending

    # ===== Batch distillation (journal -> claims, ADR 0017) =====

    async def maybe_distill_completed_sessions(self, logs: SessionLogReader) -> int | None:
        """Distill completed sessions not yet covered by the watermark.

        Called by the scheduler at its session reap point. When the
        pending count reaches ``config.distillation_window`` the whole
        batch is distilled from the sessions' journals into claims; on
        success the watermark advances to the batch's latest session
        start, on failure it stays put and the next reap retries.

        Returns:
            The number of claims persisted if a batch was distilled,
            None otherwise
        """
        if not self._loaded:
            await self.load()

        pending = await self._collect_pending(logs)
        if len(pending) < self.config.distillation_window:
            logger.debug(
                "distillation_window_not_reached",
                pending_count=len(pending),
                window=self.config.distillation_window,
            )
            return None

        projections = [
            self._build_projection(session_id, events)
            for session_id, _started_at, events in pending
        ]
        return await self._distill_sessions(
            projections, [started_at for _sid, started_at, _events in pending]
        )

    @staticmethod
    def _build_projection(session_id: str, events: list[dict[str, Any]]) -> SessionProjection:
        """Rebuild one session's distillation view from its journal events.

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

    async def _distill_sessions(
        self,
        projections: list[SessionProjection],
        started_ats: list[datetime | None],
    ) -> int | None:
        """Distill a batch of session projections into claims."""
        source_session_ids = [p.session_id for p in projections]
        logger.info("distillation_started", session_count=len(projections))

        claim_dicts: list[dict[str, Any]] = []
        if self.distiller:
            try:
                claim_dicts = await self.distiller.distill(projections)
            except Exception as e:
                logger.error("llm_distillation_failed", error=str(e), exc_info=True)
        if not claim_dicts:
            # No LLM, LLM failure, or nothing usable: mechanical claims —
            # distillation must never lose a batch to provider flakiness.
            claim_dicts = Distiller.fallback_claims(projections)

        if not claim_dicts:
            logger.warning("distillation_no_claims", session_count=len(projections))
            return None

        now = datetime.now().isoformat()
        persisted: list[dict[str, Any]] = []
        try:
            for claim_dict in claim_dicts:
                claim = Claim(
                    text=claim_dict["text"],
                    timestamp=now,
                    valid_from=now,
                    source_session_ids=source_session_ids,
                    importance=claim_dict.get("importance", 0.5),
                )
                claim_data = claim.to_dict()
                await self.claims.add_claim(claim_data)
                persisted.append(claim_data)
        except Exception as e:
            # Persistence failure: the watermark stays, the next reap
            # retries the same batch.
            logger.error("distillation_persist_failed", error=str(e), exc_info=True)
            return None

        await self._index_claims(persisted)

        known_starts = [ts for ts in started_ats if ts is not None]
        if known_starts:
            await self._save_watermark(max(known_starts).isoformat())

        logger.info(
            "distillation_completed",
            session_count=len(projections),
            claim_count=len(claim_dicts),
        )
        return len(claim_dicts)

    async def build_context_blocks(
        self,
        current_message: Any | None = None,
    ) -> list[ContextBlock]:
        """Build context blocks for LLM context injection.

        With a current message and a usable vector index this is JIT
        retrieval (ADR 0017 stage 2): the message is embedded, a
        similarity-ranked candidate pool is pulled from the index, and
        the top ``config.context_max_claims`` active claims by the
        composite score (α·similarity + β·recency + γ·importance) are
        injected. Without a query, without an embedding model, or on any
        retrieval failure the behavior degrades to the most recent
        active claims — degradation is a first-class mode, never an
        error.

        Args:
            current_message: The current message being processed; its
                text is the relevance query for JIT retrieval

        Returns:
            List of ContextBlock objects ordered by priority
        """
        if not self._loaded:
            await self.load()

        query = str(current_message).strip() if current_message is not None else ""
        claims = (
            await self._retrieve_relevant(query, self.config.context_max_claims)
            if query
            else None
        )
        if claims is None:
            claims = await self.claims.get_recent_claims(self.config.context_max_claims)
        if not claims:
            return []

        claim_lines = []
        claim_ids: list[str] = []
        for c in claims:
            text = str(c.get("text", ""))
            timestamp = str(c.get("timestamp", ""))[:10]  # Just date
            if text:
                claim_lines.append(f"- [{timestamp}] {text}")
                if (cid := c.get("id")) is not None:
                    claim_ids.append(str(cid))

        if not claim_lines:
            return []

        return [
            ContextBlock(
                block_type="claims",
                priority=1,
                content="\n".join(claim_lines),
                source_ids=claim_ids,
            )
        ]

    async def _retrieve_relevant(self, query: str, k: int) -> list[dict[str, Any]] | None:
        """JIT retrieval: composite-scored top-k active claims.

        Returns None to signal degradation (no index, embedding failure,
        or nothing indexed) — the caller then falls back to recency
        order. Superseded/deleted claims are skipped even when a stale
        index row still points at them.
        """
        if self._index is None or self._embedder is None or k <= 0:
            return None
        try:
            (query_vector,) = await self._embedder.embed([query])
        except Exception as e:
            logger.warning("jit_query_embed_failed", error=str(e))
            return None

        active = {str(c.get("id")): c for c in await self.claims.get_all_claims()}
        pool = min(len(active), max(k * _RETRIEVAL_POOL_FACTOR, k))
        now = datetime.now()
        scored: list[tuple[float, dict[str, Any]]] = []
        try:
            for claim_id, similarity in self._index.search(query_vector, pool):
                claim = active.get(claim_id)
                if claim is None:
                    continue
                score = (
                    _SCORE_ALPHA_SIMILARITY * similarity
                    + _SCORE_BETA_RECENCY * self._recency_score(claim, now)
                    + _SCORE_GAMMA_IMPORTANCE * self._importance_score(claim)
                )
                scored.append((score, claim))
        except Exception as e:
            logger.warning("jit_retrieval_failed", error=str(e))
            return None
        if not scored:
            return None
        scored.sort(key=lambda item: item[0], reverse=True)
        return [claim for _score, claim in scored[:k]]

    @staticmethod
    def _importance_score(claim: dict[str, Any]) -> float:
        """The claim's importance clamped to [0, 1]; a corrupt value reads
        as the default (0.5) — hand-edited claims must never break JIT."""
        try:
            return min(1.0, max(0.0, float(claim.get("importance", 0.5))))
        except (TypeError, ValueError):
            return 0.5

    @staticmethod
    def _recency_score(claim: dict[str, Any], now: datetime) -> float:
        """Exponential decay of the claim's age: 1.0 just now, halving
        every ``_RECENCY_HALF_LIFE_DAYS``. An unparseable timestamp reads
        as neutral (0.5) — a corrupt field must never break retrieval."""
        try:
            timestamp = datetime.fromisoformat(str(claim.get("timestamp", "")))
            age_days = max(0.0, (now - timestamp).total_seconds()) / 86400.0
        except (ValueError, TypeError):
            return 0.5
        return float(0.5 ** (age_days / _RECENCY_HALF_LIFE_DAYS))

    # ===== LLM Tool Operations =====

    async def remember_fact(
        self,
        fact: str,
        importance: float = 0.5,
    ) -> str:
        """Record a claim immediately (called by LLM via tool).

        Args:
            fact: Natural language description of what to remember
            importance: Importance score (0-1)

        Returns:
            The claim ID
        """
        if not self._loaded:
            await self.load()

        now = datetime.now().isoformat()
        claim = Claim(
            text=fact,
            timestamp=now,
            valid_from=now,
            source_session_ids=[],
            importance=importance,
        )

        claim_data = claim.to_dict()
        claim_id = await self.claims.add_claim(claim_data)
        await self._index_claims([claim_data])
        logger.info("claim_recorded", claim_id=claim_id, content=fact)

        return claim_id

    async def search_memories(
        self,
        query: str,
        limit: int = 5,
    ) -> str:
        """Search memories (called by LLM via tool).

        Vector retrieval when the index is available (the embedding
        query is naturally cross-lingual — the old space-split keyword
        matching is gone with ); otherwise a keyword fallback. Each
        hit carries its date and source session references, so the agent
        can cite where a memory came from.

        Args:
            query: Search query string
            limit: Maximum results to return

        Returns:
            Formatted search results as text
        """
        if not self._loaded:
            await self.load()

        results = await self._vector_search(query, limit)
        if results is None:
            keywords = [kw.strip() for kw in query.split() if len(kw.strip()) > 1]
            if not keywords:
                keywords = [query]
            results = await self.claims.search_claims_by_keyword(keywords, limit)

        lines = [f"搜索结果 for '{query}':", ""]
        if results:
            lines.append("## 相关记忆")
            for c in results:
                timestamp = str(c.get("timestamp", ""))[:10]
                refs = [str(s) for s in c.get("source_session_ids") or []]
                suffix = f"（来源 session: {', '.join(refs)}）" if refs else ""
                lines.append(f"- [{timestamp}] {c.get('text', '')}{suffix}")
            lines.append("")
        else:
            lines.append("未找到相关记忆。")

        return "\n".join(lines)

    async def _vector_search(self, query: str, limit: int) -> list[dict[str, Any]] | None:
        """Similarity-ranked active claims for ``query``; None = degrade.

        Ranked by similarity alone: an explicit search query is the
        intent, and recency/importance weighting would distort it.
        """
        if self._index is None or self._embedder is None:
            return None
        try:
            (query_vector,) = await self._embedder.embed([query])
        except Exception as e:
            logger.warning("memory_search_embed_failed", error=str(e))
            return None
        active = {str(c.get("id")): c for c in await self.claims.get_all_claims()}
        return [
            claim
            for claim_id, _similarity in self._index.search(query_vector, limit)
            if (claim := active.get(claim_id)) is not None
        ]

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

        keywords = [kw.strip() for kw in query.split() if len(kw.strip()) > 1]
        if not keywords:
            keywords = [query]

        matches = await self.claims.search_claims_by_keyword(keywords, 100)

        if not matches:
            return f"未找到匹配 '{query}' 的记忆。"

        if not confirm:
            # Preview mode
            lines = [f"找到 {len(matches)} 条匹配记忆:", ""]
            for c in matches[:5]:
                lines.append(f"- {c.get('text', '')}")
            if len(matches) > 5:
                lines.append(f"- … 等共 {len(matches)} 条")
            lines.append("")
            lines.append("调用 forget(query='...', confirm=True) 确认删除。")
            return "\n".join(lines)

        # Delete mode
        deleted_count = 0
        for c in matches:
            if (cid := c.get("id")) is not None and await self.claims.delete_claim(str(cid)):
                deleted_count += 1

        return f"已删除 {deleted_count} 条记忆。"

    async def delete_claim(self, claim_id: str) -> bool:
        """Delete one claim by ID (called by the reflection engine).

        The reflection prompt lists claims by an ID prefix, so an exact
        miss falls back to unique-prefix resolution. Unknown or ambiguous
        IDs delete nothing and are logged — never a silent no-op.

        Args:
            claim_id: The claim's full ID or an ID prefix

        Returns:
            True if exactly one claim was deleted
        """
        if not self._loaded:
            await self.load()

        if await self.claims.delete_claim(claim_id):
            self._remove_from_index(claim_id)
            logger.info("claim_deleted", agent_id=self.agent_id, claim_id=claim_id)
            return True

        all_claims = await self.claims.get_all_claims(include_superseded=True)
        matches = [c for c in all_claims if str(c.get("id", "")).startswith(claim_id)]
        if len(matches) == 1:
            resolved_id = str(matches[0]["id"])
            await self.claims.delete_claim(resolved_id)
            self._remove_from_index(resolved_id)
            logger.info(
                "claim_deleted",
                agent_id=self.agent_id,
                claim_id=resolved_id,
                requested_id=claim_id,
            )
            return True

        if matches:
            logger.warning(
                "claim_delete_ambiguous",
                agent_id=self.agent_id,
                claim_id=claim_id,
                match_count=len(matches),
            )
        else:
            logger.warning(
                "claim_delete_not_found", agent_id=self.agent_id, claim_id=claim_id
            )
        return False

    def _remove_from_index(self, claim_id: str) -> None:
        """Drop a deleted claim's vector (best-effort; a stale row is
        otherwise harmless — retrieval skips non-active claims)."""
        if self._index is not None:
            try:
                self._index.delete(claim_id)
            except Exception as e:
                logger.warning("vector_index_delete_failed", claim_id=claim_id, error=str(e))

    async def get_memory_digest(self, logs: SessionLogReader) -> dict[str, Any]:
        """Get a digest of current memory state for reflection/audit.

        Returns a dict with the recent active claims and the number of
        completed sessions not yet distilled (newer than the watermark)
        for the reflection sub-agent to analyze.

        Returns:
            Dict with keys: undistilled_session_count, claim_count, claims
        """
        if not self._loaded:
            await self.load()

        claims = await self.claims.get_recent_claims(50)
        pending = await self._collect_pending(logs)

        return {
            "undistilled_session_count": len(pending),
            "claim_count": len(claims),
            "claims": claims,
        }
