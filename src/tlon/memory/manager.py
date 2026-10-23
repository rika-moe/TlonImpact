"""Memory Manager - the claim-based memory interface (ADR 0017).

The injected history reader exposes legacy journal and module program facts;
completed sources are distilled in batches into claims — natural-language
assertions with provenance — stored append-only in ``claims.jsonl``.
Distillation progress is a set of consumed source IDs and a durable pending
batch in ``distillation.json``. Late completion never depends on start-time order. Retrieval is JIT: the current
mail embeds into a query against a sqlite-vec index (``claims_vec.db``,
a pure derived view of the claim store) and active claims are ranked by
a composite similarity/recency/importance score; without an embedding
model or on any index failure it degrades to recency order. Writes on
both paths (batch distillation, ``remember_fact``) pass conflict
judgment first: similar active old claims are LLM-judged, and
contradicted ones are superseded in place — invalidated, never deleted
(ADR 0017 stage 3). Ordinary judgment failures degrade to pure append;
execution control and recovery identity failures propagate without writing. The manager
keeps the claim store, the index, the context-block assembly for the
``(knowledge)`` section, and the batch distillation trigger the
scheduler calls at its session reap point.
"""

import asyncio
import contextlib
import hashlib
import json
import os
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import aiofiles

from tlon.base.config import LLMConfig
from tlon.base.embedding_client import EmbeddingClient
from tlon.base.execution import (
    BudgetExceededError,
    ExecutionDeadlineError,
    ExecutionScope,
    MemberWriteGate,
    ModelRequestRejectedError,
    UnknownModelOutcomeError,
    bind_execution,
    current_execution,
)
from tlon.base.logger import get_logger
from tlon.base.maintenance import MaintenanceCheckpointError
from tlon.base.paths import TlonPaths
from tlon.operations.schema import RecoveryMismatchError

from .claim_store import ClaimStore
from .distiller import Distiller
from .models import Claim, ContextBlock, MemoryConfig, SessionProjection, build_session_projection
from .vector_index import VectorIndex

logger = get_logger(__name__)


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
    these methods qualifies — in production, ``SessionJournal``.
    """

    async def list_completed(self, started_after: datetime | None = None) -> list[str]:
        """Completed source IDs; callers consume by stable identity."""
        ...

    async def count_completed(self, started_after: datetime | None = None) -> int:
        """Count completed sources with the optional historical filter."""
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
        data_dir: Path,
        llm_config: LLMConfig | None = None,
        tlon_paths: TlonPaths | None = None,
        embedder: Embedder | None = None,
        write_gate: MemberWriteGate | None = None,
    ) -> None:
        """Initialize Memory Manager.

        Args:
            agent_id: The agent ID
            config: Memory configuration
            data_dir: The agent's home directory (``{home}/{agent_id}``);
                the memory dir resolves as ``{data_dir}/memory``. The
                registry always passes an explicit data_dir (unified
                assembly, ADR 0006).
            llm_config: Optional LLM config for distillation; its
                ``embedding_model_name`` (when set) also builds the
                embedding client for vector retrieval
            tlon_paths: Path resolver handed to the distiller's LLM
                client for ledger attribution (ADR 0014); required
                when ``llm_config`` is set.
            embedder: Embedding client override (tests inject a fake);
                None = build from ``llm_config.embedding_model_name``,
                and when that is unset too, no vector retrieval at all
            write_gate: Shared member mutation gate; standalone instances
                create their own gate when omitted.
        """
        self.agent_id = agent_id
        self.write_gate = write_gate if write_gate is not None else MemberWriteGate()
        self.config = config

        self.storage_path = data_dir / "memory"
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.config_file = self.storage_path / "config.json"

        self.distillation_file = self.storage_path / "distillation.json"

        self.claims = ClaimStore(agent_id=agent_id, storage_path=self.storage_path)

        self.maintenance_error: str | None = None
        self._loaded = False
        self._consumed: set[str] = set()
        self._pending_batch: dict[str, Any] | None = None

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
        distillation source receipts.

        ``memory/config.json`` is the single per-agent tuning surface:
        when it exists it replaces the constructor-provided config (the
        registry passes defaults), so a human-edited file takes effect.
        A missing file is created with the current defaults; a corrupt
        one is logged and re-initialized with defaults.
        """
        async with self.write_gate.hold():
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
                except (
                    BudgetExceededError,
                    ExecutionDeadlineError,
                    UnknownModelOutcomeError,
                    ModelRequestRejectedError,
                    RecoveryMismatchError,
                ):
                    raise
                except Exception as e:
                    logger.error("memory_config_load_failed", error=str(e), exc_info=True)
                    self.config.agent_id = self.agent_id
                    await self._save_config()
            else:
                # Save initial config
                self.config.agent_id = self.agent_id
                await self._save_config()

            try:
                self._load_distillation()
            except MaintenanceCheckpointError as error:
                self.maintenance_error = str(error)
                logger.error("distillation_checkpoint_invalid", error=str(error))
            await self.claims.load()
            # Only untagged historical claims establish old consumption. New
            # claims carry their origin; provenance from remember/reflection
            # is never a receipt for the independent distillation consumer.
            pending_ids = set(self._pending_batch["source_ids"]) if self._pending_batch else set()
            for claim in await self.claims.get_all_claims(include_superseded=True):
                if "origin" in claim:
                    continue
                self._consumed.update(
                    source_id
                    for source_id in claim.get("source_session_ids", [])
                    if not source_id.startswith("program:") and source_id not in pending_ids
                )
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
        async with self.write_gate.hold():
            self._index = None
            if self._embedder is None:
                return
            try:
                index = VectorIndex(self.storage_path / "claims_vec.db")
            except (
                BudgetExceededError,
                ExecutionDeadlineError,
                UnknownModelOutcomeError,
                ModelRequestRejectedError,
                RecoveryMismatchError,
            ):
                raise
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
                    [(str(c["id"]), vector) for c, vector in zip(active, vectors, strict=True)],
                )
                logger.info("vector_index_rebuilt", claim_count=len(active))
            except (
                BudgetExceededError,
                ExecutionDeadlineError,
                UnknownModelOutcomeError,
                ModelRequestRejectedError,
                RecoveryMismatchError,
            ):
                raise
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
        async with self.write_gate.hold():
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
            except (
                BudgetExceededError,
                ExecutionDeadlineError,
                UnknownModelOutcomeError,
                ModelRequestRejectedError,
                RecoveryMismatchError,
            ):
                raise
            except Exception as e:
                logger.warning("vector_index_backfill_failed", error=str(e))

    async def _index_claims(self, claims: list[dict[str, Any]]) -> None:
        """Best-effort: embed freshly written claims into the index.

        Failures only log — an unindexed claim stays reachable via the
        recency fallback, and the next startup's backfill retries it.
        """
        async with self.write_gate.hold():
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
                except (
                    BudgetExceededError,
                    ExecutionDeadlineError,
                    UnknownModelOutcomeError,
                    ModelRequestRejectedError,
                    RecoveryMismatchError,
                ):
                    raise
                except Exception as e:
                    logger.warning("vector_index_upsert_failed", error=str(e))

    async def _save_config(self) -> None:
        """Save memory config to disk."""
        async with self.write_gate.hold():
            try:
                async with aiofiles.open(self.config_file, "w", encoding="utf-8") as f:
                    await f.write(json.dumps(self.config.to_dict(), indent=2, ensure_ascii=False))
            except (
                BudgetExceededError,
                ExecutionDeadlineError,
                UnknownModelOutcomeError,
                ModelRequestRejectedError,
                RecoveryMismatchError,
            ):
                raise
            except Exception as e:
                logger.error("memory_config_save_failed", error=str(e), exc_info=True)

    # ===== Distillation source receipts =====

    def _load_distillation(self) -> None:
        """Load source receipts. Old scalar timestamps are not consumption evidence."""
        if not self.distillation_file.exists():
            return
        try:
            data = json.loads(self.distillation_file.read_text(encoding="utf-8"))
            if (
                isinstance(data, dict)
                and set(data) == {"last_distilled_at"}
                and isinstance(data["last_distilled_at"], str)
            ):
                return
            self._validate_distillation(data)
        except (UnicodeDecodeError, ValueError) as error:
            raise MaintenanceCheckpointError(self.distillation_file, str(error)) from error
        self._consumed = set(data["consumed_source_ids"])
        self._pending_batch = data["pending"]

    @staticmethod
    def _validate_distillation(data: Any) -> None:
        """Validate frozen data before allowing maintenance effects."""

        def strings(value: Any) -> bool:
            return isinstance(value, list) and all(isinstance(item, str) for item in value)

        if not isinstance(data, dict) or not strings(data.get("consumed_source_ids")):
            raise ValueError("consumed_source_ids must be a list of strings")
        if set(data) != {"consumed_source_ids", "pending"}:
            raise ValueError("invalid distillation checkpoint fields")
        batch = data["pending"]
        if batch is None:
            return
        if not isinstance(batch, dict) or not isinstance(batch.get("id"), str):
            raise ValueError("pending batch requires a string id")
        if set(batch) - {
            "id",
            "source_ids",
            "projections",
            "claims",
            "conflict_candidates",
            "supersessions",
        }:
            raise ValueError("invalid pending batch fields")
        if not strings(batch.get("source_ids")):
            raise ValueError("pending source_ids must be a list of strings")
        projections = batch.get("projections")
        if not isinstance(projections, list):
            raise ValueError("pending projections must be a list")
        allowed = {
            "session_id",
            "trigger_content",
            "llm_responses",
            "tool_results",
            "end_reason",
            "program_result",
            "errors",
        }
        for projection in projections:
            if not isinstance(projection, dict) or set(projection) - allowed:
                raise ValueError("invalid pending projection fields")
            for key in ("session_id", "trigger_content"):
                if not isinstance(projection.get(key), str):
                    raise ValueError(f"projection {key} must be a string")
            for key in ("end_reason", "program_result"):
                if key in projection and not isinstance(projection[key], str):
                    raise ValueError(f"projection {key} must be a string")
            for key in ("llm_responses", "tool_results", "errors"):
                if key in projection and not strings(projection[key]):
                    raise ValueError(f"projection {key} must be a list of strings")
        if [item["session_id"] for item in projections] != batch["source_ids"]:
            raise ValueError("projection identities do not match source_ids")
        for key in ("claims", "conflict_candidates"):
            if key not in batch:
                continue
            if not isinstance(batch[key], list):
                raise ValueError(f"pending {key} must be a list")
            for claim in batch[key]:
                if not isinstance(claim, dict):
                    raise ValueError(f"pending {key} contains a non-object claim")
                for field in ("id", "text", "timestamp", "valid_from"):
                    if not isinstance(claim.get(field), str):
                        raise ValueError(f"pending claim {field} must be a string")
                if not strings(claim.get("source_session_ids")):
                    raise ValueError("pending claim source_session_ids must be strings")
                for field in ("valid_to", "superseded_by"):
                    if claim.get(field) is not None and not isinstance(claim[field], str):
                        raise ValueError(f"pending claim {field} must be a string or null")
                if claim.get("status", "active") not in ("active", "superseded"):
                    raise ValueError("invalid pending claim status")
                if not isinstance(claim.get("importance"), (int, float)):
                    raise ValueError("pending claim importance must be a number")
        if "supersessions" in batch:
            plan = batch["supersessions"]
            if not isinstance(plan, list) or any(
                not isinstance(item, dict)
                or any(
                    not isinstance(item.get(key), str) for key in ("old_id", "new_id", "valid_to")
                )
                for item in plan
            ):
                raise ValueError("invalid pending supersessions")

    async def _save_distillation(self) -> None:
        """Commit progress atomically; an unacknowledged write remains retryable."""
        temporary = self.distillation_file.with_name(self.distillation_file.name + ".tmp")
        try:
            async with aiofiles.open(temporary, "w", encoding="utf-8") as stream:
                await stream.write(
                    json.dumps(
                        {
                            "consumed_source_ids": sorted(self._consumed),
                            "pending": self._pending_batch,
                        },
                        ensure_ascii=False,
                    )
                )
                await stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.distillation_file)
        finally:
            with contextlib.suppress(OSError):
                temporary.unlink(missing_ok=True)

    async def _collect_pending(
        self, logs: SessionLogReader
    ) -> list[tuple[str, list[dict[str, Any]]]]:
        return [
            (source_id, await logs.read_events(source_id))
            for source_id in sorted(await logs.list_completed())
            if source_id not in self._consumed
        ]

    async def maybe_distill_completed_sessions(self, logs: SessionLogReader) -> int | None:
        """Consume stable completed source IDs, including late-arriving older runs."""
        async with self.write_gate.hold():
            if not self._loaded:
                await self.load()
            if self.maintenance_error is not None:
                raise MaintenanceCheckpointError(self.distillation_file, self.maintenance_error)
            if self._pending_batch is not None:
                projections = [
                    SessionProjection(**item) for item in self._pending_batch["projections"]
                ]
                return await self._distill_sessions(projections)
            pending = await self._collect_pending(logs)
            if len(pending) < self.config.distillation_window:
                return None
            return await self._distill_sessions(
                [build_session_projection(events, source_id) for source_id, events in pending]
            )

    async def _distill_sessions(self, projections: list[SessionProjection]) -> int | None:
        """Persist a fixed batch before modeling, then replay its receipts and writes."""
        async with self.write_gate.hold():
            if self._pending_batch is None:
                projections = sorted(projections, key=lambda item: item.session_id)
                source_ids = [item.session_id for item in projections]
                batch_id = hashlib.sha256(json.dumps(source_ids).encode()).hexdigest()
                self._pending_batch = {
                    "id": batch_id,
                    "source_ids": source_ids,
                    "projections": [asdict(item) for item in projections],
                }
            batch = self._pending_batch
            # A prior save may have raised before (or after) its atomic replace.
            # Persist the complete frozen state before any further effect.
            await self._save_distillation()
            outer = current_execution()
            scope = (
                ExecutionScope(
                    outer.store,
                    f"maintenance:distill:{batch['id']}",
                    outer.token_limit,
                    outer.measure,
                    deadline=outer.effective_deadline(),
                    parent=outer,
                )
                if outer is not None
                else None
            )
            if scope is not None:
                await scope.initialize()
                scope.check()
            if "claims" not in batch:
                with bind_execution(scope.child("distill") if scope else None):
                    claim_dicts = (
                        await self.distiller.distill(projections) if self.distiller else []
                    )
                if not claim_dicts:
                    claim_dicts = Distiller.fallback_claims(projections)
                now = datetime.now(UTC).isoformat()
                batch["claims"] = [
                    Claim(
                        id=f"distill:{batch['id']}:{index}",
                        origin="distillation",
                        text=item["text"],
                        timestamp=now,
                        valid_from=now,
                        source_session_ids=batch["source_ids"],
                        importance=item.get("importance", 0.5),
                    ).to_dict()
                    for index, item in enumerate(claim_dicts)
                ]
                await self._save_distillation()
            new_claims = [Claim.from_dict(item) for item in batch["claims"]]
            if "supersessions" not in batch:
                with bind_execution(scope.child("conflict") if scope else None):
                    batch["supersessions"] = await self._plan_conflicts(new_claims, batch=batch)
                await self._save_distillation()
            await self._apply_conflicts(batch["supersessions"])
            for claim in batch["claims"]:
                if await self.claims.get_claim(claim["id"]) is None:
                    await self.claims.add_claim(claim)
            await self._index_claims(batch["claims"])
            old_consumed = self._consumed.copy()
            self._consumed.update(batch["source_ids"])
            self._pending_batch = None
            try:
                await self._save_distillation()
            except BaseException:
                self._consumed = old_consumed
                self._pending_batch = batch
                raise
            return len(new_claims)

    # ===== Conflict judgment (ADR 0017 stage 3) =====

    async def _resolve_conflicts(self, new_claims: list[Claim]) -> None:
        await self._apply_conflicts(await self._plan_conflicts(new_claims))

    async def _apply_conflicts(self, plan: list[dict[str, str]]) -> None:
        for item in plan:
            await self.claims.supersede_claim(item["old_id"], item["new_id"], item["valid_to"])

    async def _plan_conflicts(
        self, new_claims: list[Claim], *, batch: dict[str, Any] | None = None
    ) -> list[dict[str, str]]:
        """Supersede old claims contradicted by freshly built new ones.

        Both write paths (batch distillation, ``remember_fact``) run the
        same judgment before appending: each new claim's text is embedded,
        the index is searched for similar **active** old claims
        (similarity at or above ``config.conflict_similarity_threshold``),
        and the whole batch goes to the distiller's one-shot LLM judgment.
        SUPERSEDE verdicts invalidate the old claims in place
        (``status``/``superseded_by``/``valid_to`` — never deleted); the
        new claims themselves are appended by the caller.

        Ordinary failures — no distiller (mechanical fallback claims
        imply the LLM is down anyway), no index/embedder, embedding or
        search failure, judgment failure — degrades to pure append:
        conflict judgment is an enhancement. Execution control and recovery
        identity errors propagate so a rejected or unknown model call cannot
        silently become a successful memory write.
        """
        async with self.write_gate.hold():
            if self.distiller is None or self._index is None or self._embedder is None:
                return []
            if not new_claims:
                return []

            texts = [claim.text for claim in new_claims]
            if batch is not None and "conflict_candidates" in batch:
                candidates: list[dict[str, Any]] = batch["conflict_candidates"]
            else:
                try:
                    vectors = await self._embedder.embed(texts)
                except (
                    BudgetExceededError,
                    ExecutionDeadlineError,
                    UnknownModelOutcomeError,
                    ModelRequestRejectedError,
                    RecoveryMismatchError,
                ):
                    raise
                except Exception as e:
                    logger.warning("conflict_candidate_embed_failed", error=str(e))
                    return []

                active = {str(c.get("id")): c for c in await self.claims.get_all_claims()}
                candidates = []
                seen: set[str] = set()
                try:
                    for vector in vectors:
                        for claim_id, similarity in self._index.search(
                            vector, self.config.conflict_candidate_limit
                        ):
                            if similarity < self.config.conflict_similarity_threshold:
                                continue
                            if claim_id in seen or claim_id not in active:
                                continue
                            seen.add(claim_id)
                            candidates.append(active[claim_id])
                except (
                    BudgetExceededError,
                    ExecutionDeadlineError,
                    UnknownModelOutcomeError,
                    ModelRequestRejectedError,
                    RecoveryMismatchError,
                ):
                    raise
                except Exception as e:
                    logger.warning("conflict_candidate_search_failed", error=str(e))
                    return []
                if batch is not None:
                    batch["conflict_candidates"] = candidates
                    await self._save_distillation()
            if not candidates:
                return []

            try:
                verdicts = await self.distiller.judge_conflicts(texts, candidates)
            except (
                BudgetExceededError,
                ExecutionDeadlineError,
                UnknownModelOutcomeError,
                ModelRequestRejectedError,
                RecoveryMismatchError,
            ):
                raise
            except Exception as e:
                if current_execution() is not None:
                    raise
                logger.warning("conflict_judgment_failed", error=str(e))
                return []
            if verdicts is None:
                return []

            now = datetime.now(UTC).isoformat()
            return [
                {"old_id": old_id, "new_id": claim.id, "valid_to": now}
                for claim, verdict in zip(new_claims, verdicts, strict=True)
                if verdict.action == "supersede"
                for old_id in verdict.superseded_ids
                if old_id != claim.id
            ]

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
            await self._retrieve_relevant(query, self.config.context_max_claims) if query else None
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
        except (
            BudgetExceededError,
            ExecutionDeadlineError,
            UnknownModelOutcomeError,
            RecoveryMismatchError,
        ):
            raise
        except Exception as e:
            logger.warning("jit_query_embed_failed", error=str(e))
            return None

        active = {str(c.get("id")): c for c in await self.claims.get_all_claims()}
        pool = min(len(active), max(k * self.config.retrieval_pool_factor, k))
        now = datetime.now(UTC)
        scored: list[tuple[float, dict[str, Any]]] = []
        try:
            for claim_id, similarity in self._index.search(query_vector, pool):
                claim = active.get(claim_id)
                if claim is None:
                    continue
                score = (
                    self.config.retrieval_alpha_similarity * similarity
                    + self.config.retrieval_beta_recency * self._recency_score(claim, now)
                    + self.config.retrieval_gamma_importance * self._importance_score(claim)
                )
                scored.append((score, claim))
        except (
            BudgetExceededError,
            ExecutionDeadlineError,
            UnknownModelOutcomeError,
            RecoveryMismatchError,
        ):
            raise
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

    def _recency_score(self, claim: dict[str, Any], now: datetime) -> float:
        """Exponential decay of the claim's age: 1.0 just now, halving
        every ``config.retrieval_recency_half_life_days``. An unparseable
        timestamp reads as neutral (0.5) — a corrupt field must never
        break retrieval."""
        try:
            timestamp = datetime.fromisoformat(str(claim.get("timestamp", "")))
            age_days = max(0.0, (now - timestamp).total_seconds()) / 86400.0
        except (ValueError, TypeError):
            return 0.5
        return float(0.5 ** (age_days / self.config.retrieval_recency_half_life_days))

    # ===== LLM Tool Operations =====

    async def remember_fact(
        self,
        fact: str,
        importance: float = 0.5,
        *,
        claim_id: str | None = None,
        source_session_ids: list[str] | None = None,
    ) -> str:
        """Record a claim immediately (called by LLM via tool).

        The claim goes through the same conflict judgment as distilled
        claims (ADR 0017): similar active old claims are judged, and
        contradicted ones are superseded before the new claim appends.

        Args:
            fact: Natural language description of what to remember
            importance: Importance score (0-1)

        Returns:
            The claim ID
        """
        async with self.write_gate.hold():
            if not self._loaded:
                await self.load()

            if claim_id is not None and await self.claims.get_claim(claim_id) is not None:
                return claim_id
            now = datetime.now(UTC).isoformat()
            claim = Claim(
                text=fact,
                timestamp=now,
                valid_from=now,
                source_session_ids=source_session_ids or [],
                importance=importance,
            )

            if claim_id is not None:
                claim.id = claim_id
            await self._resolve_conflicts([claim])

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
        except (
            BudgetExceededError,
            ExecutionDeadlineError,
            UnknownModelOutcomeError,
            RecoveryMismatchError,
        ):
            raise
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
        async with self.write_gate.hold():
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
                lines.append("调用 memory-forget，参数 query 为检索词、confirm 为 true，确认删除。")
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
        async with self.write_gate.hold():
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
                logger.warning("claim_delete_not_found", agent_id=self.agent_id, claim_id=claim_id)
            return False

    def _remove_from_index(self, claim_id: str) -> None:
        """Drop a deleted claim's vector (best-effort; a stale row is
        otherwise harmless — retrieval skips non-active claims)."""
        if self._index is not None:
            try:
                self._index.delete(claim_id)
            except (
                BudgetExceededError,
                ExecutionDeadlineError,
                UnknownModelOutcomeError,
                ModelRequestRejectedError,
                RecoveryMismatchError,
            ):
                raise
            except Exception as e:
                logger.warning("vector_index_delete_failed", claim_id=claim_id, error=str(e))

    async def get_memory_digest(self, logs: SessionLogReader) -> dict[str, Any]:
        """Get a digest of current memory state for reflection/audit.

        Returns a dict with the recent active claims and the number of
        completed sources not yet consumed
        for the reflection sub-agent to analyze.

        Returns:
            Dict with keys: undistilled_session_count, claim_count, claims
        """
        if not self._loaded:
            await self.load()

        claims = await self.claims.get_recent_claims(50)
        completed = await logs.list_completed()
        pending_count = len(set(completed) - self._consumed)
        recent_interactions = [
            asdict(build_session_projection(await logs.read_events(sid), sid))
            for sid in completed[-10:]
        ]

        for item in recent_interactions:
            item["trigger_content"] = item["trigger_content"][:300]
            item["llm_responses"] = [text[:600] for text in item["llm_responses"][-3:]]
            item["tool_results"] = [text[:300] for text in item["tool_results"][-5:]]
            item["program_result"] = item.get("program_result", "")[:600]
            item["errors"] = [text[:300] for text in item.get("errors", [])[-5:]]
        return {
            "undistilled_session_count": pending_count,
            "claim_count": len(claims),
            "claims": claims,
            "recent_interactions": recent_interactions,
        }
