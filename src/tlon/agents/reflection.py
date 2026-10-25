"""Reflection engine for Agent.

Agent self-driven memory audit: periodically snapshots memory state, asks
the LLM to analyze it, and applies the resulting operations (record/delete
claims). Extracted from the Agent god class.
"""

import asyncio
import contextlib
import hashlib
import json
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from importlib import resources
from typing import Any, cast

from tlon.agents.member_spec import ReflectSpec
from tlon.agents.run_journal import RunJournal
from tlon.base.execution import ExecutionScope, UnknownModelOutcomeError, bind_execution
from tlon.base.llm_client import LLMClient
from tlon.base.logger import get_logger
from tlon.base.maintenance import MaintenanceCheckpointError, MaintenanceRetry
from tlon.memory.manager import MemoryManager
from tlon.memory.models import is_empty_boot_run
from tlon.operations.schema import RecoveryMismatchError, UnknownOutcomeError

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

    Owns the durable completed-source checkpoint and running task.
    Initial enabled/interval come from explicit reflection settings;
    ``enabled`` stays a runtime switch.

    The engine receives its services (memory, LLM client, run
    journal) and reflection settings directly — no back-reference to the
    owning member runtime.
    """

    SHUTDOWN_GRACE_SECONDS = 5.0

    def __init__(
        self,
        memory: MemoryManager,
        llm_client: LLMClient,
        reflect: ReflectSpec,
        journal: RunJournal,
        state_changed: Callable[[], Awaitable[None]] | None = None,
        execution_factory: Callable[[str], ExecutionScope] | None = None,
    ):
        self._state_changed = state_changed
        self._memory = memory
        self._llm_client = llm_client
        self._journal = journal

        self._execution_factory = execution_factory
        self._checkpoint = memory.storage_path / "reflection.json"
        self.enabled: bool = reflect.enabled
        self._interval: float = reflect.interval_minutes * 60
        self._min_interactions: int = memory.config.reflection_min_interactions
        self._retry = MaintenanceRetry()
        self._running = False
        self._closing = False
        self._task: asyncio.Task[None] | None = None

    @property
    def is_running(self) -> bool:
        """A reflection (memory digest + LLM audit) is in flight right now.

        Read by the member runtime's idle accounting: an in-flight
        reflection is real background activity — the member must not be
        reported idle and drained mid-audit.
        """
        return self._running

    @property
    def diagnostic_error(self) -> str | None:
        """Latest maintenance failure for operator diagnostics."""
        return self._retry.error

    def _load_checkpoint(self) -> dict[str, Any]:
        if not self._checkpoint.exists():
            return {"seen": [], "last_completed": None, "pending": None}
        try:
            state = json.loads(self._checkpoint.read_text(encoding="utf-8"))
            self._validate_checkpoint(state)
        except (ValueError, TypeError, KeyError) as error:
            raise MaintenanceCheckpointError(self._checkpoint, str(error)) from error
        return cast(dict[str, Any], state)

    @staticmethod
    def _validate_checkpoint(state: Any) -> None:
        """Reject damaged progress without discarding frozen operations."""

        def strings(value: Any) -> bool:
            return isinstance(value, list) and all(isinstance(item, str) for item in value)

        if not isinstance(state, dict) or not strings(state.get("seen")):
            raise ValueError("seen must be a list of source IDs")
        completed = state["last_completed"]
        if completed is not None and (
            not isinstance(completed, str) or datetime.fromisoformat(completed).tzinfo is None
        ):
            raise ValueError("last_completed must be a timezone-aware timestamp or null")
        pending = state["pending"]
        if pending is None:
            return
        if not isinstance(pending, dict):
            raise ValueError("pending must be an object or null")
        if not isinstance(pending["id"], str) or not strings(pending["sources"]):
            raise ValueError("pending requires a string ID and source IDs")
        digest = pending["digest"]
        if digest is not None and (
            not isinstance(digest, dict)
            or not isinstance(digest.get("claims"), list)
            or type(digest.get("claim_count")) is not int
            or type(digest.get("undistilled_run_count")) is not int
            or not isinstance(digest.get("recent_interactions", []), list)
        ):
            raise ValueError("pending digest must contain memory counts and claims")
        operations = pending["operations"]
        if operations is not None and (
            not isinstance(operations, list)
            or not all(isinstance(operation, dict) for operation in operations)
        ):
            raise ValueError("pending operations must be objects or null")
        index = pending["next"]
        if type(index) is not int or index < 0 or index > len(operations or []):
            raise ValueError("pending next must identify an operation boundary")

    def _save_checkpoint(self, state: dict[str, Any]) -> None:
        temporary = self._checkpoint.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self._checkpoint)

    def _can_trigger(self) -> bool:
        return (
            not self._closing
            and self.enabled
            and not self._running
            and self._task is None
            and self._retry.ready()
        )

    async def check_trigger(self) -> None:
        """Count durable completed sources, retaining one frozen batch on failure."""
        if not self._can_trigger():
            return
        try:
            state = await asyncio.to_thread(self._load_checkpoint)
            if state["pending"] is None:
                sources = sorted(set(await self._journal.list_completed()) - set(state["seen"]))
                empty_boot = [
                    source
                    for source in sources
                    if is_empty_boot_run(await self._journal.read_events(source), source)
                ]
                if empty_boot:
                    state["seen"] = sorted(set(state["seen"]) | set(empty_boot))
                    await asyncio.to_thread(self._save_checkpoint, state)
                    sources = [source for source in sources if source not in empty_boot]
                if len(sources) < self._min_interactions:
                    return
                if state["last_completed"] is not None:
                    elapsed = (
                        datetime.now(UTC) - datetime.fromisoformat(state["last_completed"])
                    ).total_seconds()
                    if elapsed < self._interval:
                        return
                batch = hashlib.sha256(json.dumps(sources).encode()).hexdigest()
                state["pending"] = {
                    "id": batch,
                    "sources": sources,
                    "digest": None,
                    "operations": None,
                    "next": 0,
                }
                await asyncio.to_thread(self._save_checkpoint, state)
            if not self._can_trigger():
                return
            self._running = True
            self._task = asyncio.create_task(self._run(state))
        except Exception as error:
            self._retry.failed(
                error, blocked=isinstance(error, (RecoveryMismatchError, UnknownOutcomeError))
            )
            logger.warning("reflection_failed", error=str(error))

    def stop_accepting(self) -> None:
        """Close admission synchronously before the member stops its worker."""
        self._closing = True

    async def shutdown(self) -> None:
        """Drain existing work for one bounded window, then cancel and await it."""
        self.stop_accepting()
        task = self._task
        if task is None:
            return
        try:
            done, _ = await asyncio.wait({task}, timeout=self.SHUTDOWN_GRACE_SECONDS)
            if not done:
                task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        except asyncio.CancelledError:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            raise

    async def _run(self, state: dict[str, Any]) -> None:
        """Run an independently budgeted, replayable maintenance batch."""
        scope = None
        scope_initialized = False
        try:
            if self._state_changed is not None:
                await self._state_changed()
            pending = state["pending"]
            scope = (
                self._execution_factory("reflection:" + pending["id"])
                if self._execution_factory
                else None
            )
            with bind_execution(scope):
                if scope is not None:
                    await scope.initialize()
                    scope_initialized = True
                if pending["digest"] is None:
                    pending["digest"] = await self._memory.get_memory_digest(self._journal)
                    await asyncio.to_thread(self._save_checkpoint, state)
                digest = pending["digest"]
                if pending["operations"] is None:
                    if digest["claim_count"] == 0 and not digest.get("recent_interactions"):
                        operations = []
                    else:
                        response = await self._llm_client.chat(
                            system_prompt=REFLECTION_SYSTEM_PROMPT,
                            messages=[{"role": "user", "content": self._build_prompt(digest)}],
                            timeout=60.0,
                            purpose="reflect",
                        )
                        operations = self._parse_response(response.content or "{}")
                    pending["operations"] = await self._freeze_operations(
                        operations, digest["claims"]
                    )
                    await asyncio.to_thread(self._save_checkpoint, state)
                operations = pending["operations"]
                while pending["next"] < len(operations):
                    index = pending["next"]
                    child = scope.child(f"operation:{index}") if scope else None
                    with bind_execution(child):
                        await self._apply_operation(
                            operations[index],
                            hashlib.sha256(
                                f"reflection:{pending['id']}:{index}".encode()
                            ).hexdigest()[:32],
                            pending["sources"],
                        )
                    pending["next"] += 1
                    await asyncio.to_thread(self._save_checkpoint, state)
                state["seen"] = sorted(set(state["seen"]) | set(pending["sources"]))
                state["last_completed"] = datetime.now(UTC).isoformat()
                state["pending"] = None
                await asyncio.to_thread(self._save_checkpoint, state)
                self._retry.succeeded()
                logger.info("reflection_completed", operation_count=len(operations))
        except asyncio.CancelledError:
            if scope is not None and scope_initialized:
                budget = await asyncio.to_thread(scope.store.budget_usage, scope.root_id)
                if budget["pending_count"]:
                    self._retry.failed(
                        UnknownModelOutcomeError(
                            f"Reflection interrupted with unresolved model intent: {scope.root_id}"
                        )
                    )
                    logger.warning(
                        "reflection_outcome_unknown", root_id=scope.root_id, error=self._retry.error
                    )
            raise
        except Exception as error:
            self._retry.failed(
                error, blocked=isinstance(error, (RecoveryMismatchError, UnknownOutcomeError))
            )
            logger.warning("reflection_failed", error=str(error))
        finally:
            self._running = False
            try:
                if self._state_changed is not None:
                    await self._state_changed()
            finally:
                self._task = None

    async def _freeze_operations(
        self, operations: list[dict[str, Any]], claims: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Resolve deletion targets once, so retries cannot delete newer claims."""
        frozen = []
        for operation in operations:
            op = dict(operation)
            if op.get("action") == "delete_claim":
                prefix = op.get("claim_id", "")
                if prefix:
                    matches = [claim for claim in claims if str(claim["id"]).startswith(prefix)]
                    op["claim_ids"] = [str(matches[0]["id"])] if len(matches) == 1 else []
                    if len(matches) != 1:
                        logger.warning("reflection_delete_claim_unresolved", claim_id=prefix)
                else:
                    description = op.get("description", "")
                    keywords = [part for part in description.split() if len(part) > 1] or [
                        description
                    ]
                    matches = [
                        claim
                        for claim in claims
                        if description
                        and any(
                            keyword.lower() in str(claim.get("text", "")).lower()
                            for keyword in keywords
                        )
                    ]
                    op["claim_ids"] = [str(claim["id"]) for claim in matches]
                    if not description:
                        logger.warning("reflection_delete_claim_no_target", operation=op)
            frozen.append(op)
        return frozen

    async def _apply_operation(
        self, op: dict[str, Any], claim_id: str | None = None, sources: list[str] | None = None
    ) -> None:
        action = op.get("action", "")
        if action == "record_claim":
            await self._memory.remember_fact(
                fact=op.get("description", ""),
                importance=float(op.get("importance", 0.5)),
                claim_id=claim_id,
                source_run_ids=sources,
            )
        elif action == "delete_claim":
            for target in op.get("claim_ids", []):
                await self._memory.delete_claim(target)

    def _build_prompt(self, digest: dict[str, Any]) -> str:
        """Build the reflection prompt from memory digest."""
        parts = ["## 当前记忆状态\n"]

        parts.append(f"未蒸馏运行数：{digest['undistilled_run_count']}")
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
        if digest.get("recent_interactions"):
            parts.append("### 最近完成的交互（含来源 ID）")
            parts.append(json.dumps(digest["recent_interactions"], ensure_ascii=False))
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
