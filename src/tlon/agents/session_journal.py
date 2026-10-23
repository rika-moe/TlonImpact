"""Legacy session recovery events and read-only member-program interaction views.

Module-program views rebuild from ProgramStore on read; they never enter legacy
session recovery. Source IDs use the reserved ``program:`` namespace.

Each agent owns one ``~/.tlon/{agent_id}/sessions.sqlite3`` database. It is
the sole source of truth for legacy session state: the engine commits every state
change *before* mutating the in-memory
:class:`~tlon.agents.session.AgentSession` (the invariant
"model-visible ⇒ journaled"), and a restart rebuilds the session by
replaying that session's events.

Every event is ``{seq, ts, type, ...payload}`` — ``seq`` starts at zero and
increases within one session, ``ts`` an ISO 8601 timestamp. Event types:
``session/start``, ``context/append``, ``context/compact``,
``scratch/update``, ``iteration``, ``errors``, ``tokens``,
``llm/response``, ``tool/dispatch``, ``tool/result``, ``tool/collect``,
``state``, ``session/end``.

Replay rules:

- context = ``session/start.context`` + every ``context/append.messages``
  in order; a ``context/compact`` event (ADR 0008) replaces the context
  accumulated so far with the full post-compaction snapshot it carries.
- ``anchor_len`` (ADR 0008) = the explicit ``anchor_len`` field of
  ``session/start`` (ADR 0017: the trailing knowledge message sits outside
  the anchor); logs predating that field fall back to the length of
  ``session/start.context`` — restored onto the session by the scheduler.
- ``_all_tool_results`` = the ``tool/result`` events whose ids appear in
  some ``tool/collect`` (in collect order).
- Orphaned dispatches keep journaled results. Missing results are resolved
  through the runtime's injected invocation authority, then journaled before
  becoming ``pending_collect``. A reader cannot guess an effect outcome.
- ``session/start`` carries ``responsibility`` (ADR 0028): the wake-time
  snapshot of trigger mail ids. Replay reads it back (logs
  predating the field fall back to the empty set); the scheduler's chain
  wake compares fresh unread against it even after a crash.

The operation store decides whether recovery replays a committed outcome,
retries a safe invocation, or reports an unsafe submitted call as unknown.
Completed session metadata (``list_completed``) also feeds the
history → claims distillation trigger (ADR 0017). Consumers track stable
source IDs independently; program facts come from ProgramStore, not this log.

Writes are serialized by one asyncio lock per agent database, shared
process-wide across SessionJournal instances. SQLite transactions coordinate
other connections. A failed append rolls back and raises — the engine then
refuses to mutate the in-memory state, keeping the database and session
consistent.
"""

import asyncio
import json
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from tlon.agents.program_history import PROGRAM_SOURCE_PREFIX, program_events
from tlon.agents.program_store import ProgramStore
from tlon.agents.recovery import RecoveryBlockedError
from tlon.agents.session import SessionState
from tlon.agents.session_store import SessionStore
from tlon.base.llm_client import LLMResponse, ToolCall, validate_tool_call_ids
from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths
from tlon.operations.schema import RecoveryMismatchError
from tlon.tools.base import ToolResult

logger = get_logger(__name__)

#: Bound on the shared lock table (same magnitude as the mailbox's).
_LOCKS_CAP = 4096


#: Database-keyed locks shared by every SessionJournal over the same agent
#: (bounded LRU; a locked entry is never evicted — see mailbox._lock_for).
_LOCKS: OrderedDict[Path, asyncio.Lock] = OrderedDict()


def _completion_time(timestamp: str | None) -> float:
    """Compare persisted clocks across timezone offsets; undated sources sort first."""
    return datetime.fromisoformat(timestamp).timestamp() if timestamp else float("-inf")


def _lock_for(database: Path) -> asyncio.Lock:
    """Return the shared lock for an agent session database (bounded LRU)."""
    lock = _LOCKS.get(database)
    if lock is not None:
        _LOCKS.move_to_end(database)
        return lock
    lock = asyncio.Lock()
    _LOCKS[database] = lock
    if len(_LOCKS) > _LOCKS_CAP:
        for candidate_database, candidate in _LOCKS.items():
            if candidate_database == database:
                continue  # never evict the lock we are about to return
            if not candidate.locked():
                del _LOCKS[candidate_database]
                break
    return lock


@dataclass
class SessionReplay:
    """The session state rebuilt from one journal (``resume`` result)."""

    session_id: str
    agent_id: str
    trigger_content: str
    carrier: dict[str, str | None] | None
    recipe_source: str = ""
    source_mail_id: str | None = None
    invocation: dict[str, Any] = field(default_factory=dict)
    responsibility: list[str] = field(default_factory=list)
    context: list[dict[str, Any]] = field(default_factory=list)
    anchor_len: int = 0
    iteration: int = 0
    error_count: int = 0
    token_usage: int = 0
    last_llm_response: LLMResponse | None = None
    all_tool_results: list[dict[str, Any]] = field(default_factory=list)
    loop_scratch: dict[str, Any] = field(default_factory=dict)
    state: SessionState = SessionState.ACTIVE
    pending_collect: list[tuple[ToolCall, ToolResult]] = field(default_factory=list)
    completed: bool = False
    end_reason: str | None = None


class SessionJournal:
    """Append-only event log for one agent's sessions.

    Legacy engines write session events. Module-program interactions are
    reconstructed on demand from ProgramStore, never written to this journal.
    """

    def __init__(
        self,
        agent_id: str,
        tlon_paths: TlonPaths,
        *,
        program_store: ProgramStore | None = None,
        verify_root: Callable[[str], Awaitable[str]] | None = None,
        verify_invocation: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        resolve_dispatch: Callable[[str, ToolCall, dict[str, Any]], Awaitable[ToolResult]]
        | None = None,
    ) -> None:
        """Initialize the journal for one agent.

        Args:
            agent_id: Owning agent id (the session database lives under its home)
            tlon_paths: Path resolver (tests inject an isolated home)
        """
        self._program_store = program_store
        self._program_database = tlon_paths.agent_program_database_file(agent_id)
        self._verify_root = verify_root
        self._verify_invocation = verify_invocation
        self.resolve_dispatch = resolve_dispatch
        self._agent_id = agent_id
        self._database = tlon_paths.agent_sessions_database_file(agent_id)
        self._store = SessionStore(self._database)

    async def verify_root(self, session_id: str) -> str | None:
        """Freeze or verify runtime identity before session work."""
        return await self._verify_root(session_id) if self._verify_root is not None else None

    async def append(self, session_id: str, event_type: str, **payload: Any) -> None:
        """Commit one event and its lifecycle metadata atomically.

        Raises on I/O failure: the caller must NOT mutate the in-memory
        session when the journal write failed (the database is the truth).

        Args:
            session_id: Session the event belongs to
            event_type: One of the event types in the module docstring
            payload: JSON-serializable event payload
        """
        if session_id.startswith(PROGRAM_SOURCE_PREFIX):
            raise ValueError("Program source IDs are reserved for reading projections")
        async with _lock_for(self._database):
            try:
                await asyncio.to_thread(self._store.append, session_id, event_type, payload)
            except Exception as e:
                logger.error(
                    "session_journal_append_failed",
                    session_id=session_id,
                    event_type=event_type,
                    database_path=str(self._database),
                    error=str(e),
                    exc_info=True,
                )
                raise

    async def resume(self, session_id: str) -> SessionReplay | None:
        """Replay a session's log, rebuilding its state.

        Returns None when there is no log (or no ``session/start`` in it)
        — the caller proceeds as for a fresh session. Missing tool results
        are resolved through the injected operation authority.
        """
        if session_id.startswith(PROGRAM_SOURCE_PREFIX):
            raise ValueError("Program reading projections cannot be resumed as sessions")
        events = await self.read_events(session_id)
        if not events:
            return None
        blocked = next(
            (event for event in events if event["type"] == "session/recovery-blocked"), None
        )
        if blocked is not None:
            raise RecoveryBlockedError(str(blocked.get("error", "Session recovery blocked")))

        start: dict[str, Any] | None = None
        context: list[dict[str, Any]] = []
        anchor_len = 0
        scratch: dict[str, Any] = {}
        iteration = 0
        error_count = 0
        token_usage = 0
        last_response: LLMResponse | None = None
        state = SessionState.ACTIVE
        dispatched: dict[str, ToolCall] = {}  # dispatched, not yet collected (ordered)
        results: dict[str, tuple[ToolCall, ToolResult]] = {}
        all_tool_results: list[dict[str, Any]] = []
        completed = False
        end_reason: str | None = None

        operation_recovery: dict[str, dict[str, Any]] = {}
        for event in events:
            event_type = event.get("type")
            if start is None:
                if event_type != "session/start":
                    continue  # garbage before the start event
                start = event
                context = list(event.get("context") or [])
                # The event carries the anchor length explicitly (ADR 0017:
                # the trailing knowledge message is outside the anchor).
                # Logs written before that change lack the field — fall
                # back to the whole initial context, the old behavior.
                explicit_anchor = event.get("anchor_len")
                anchor_len = int(explicit_anchor) if explicit_anchor is not None else len(context)
                continue
            if event_type == "session/start":
                continue  # a second start event is ignored; the first wins
            if event_type == "context/append":
                context.extend(event.get("messages") or [])
            elif event_type == "context/compact":
                # ADR 0008: full replacement — the event carries the whole
                # post-compaction context, so replay stays in the compacted
                # state, byte-identical with the runtime.
                context = list(event.get("context") or [])
            elif event_type == "scratch/update":
                scratch.update(event.get("updates") or {})
            elif event_type == "iteration":
                iteration = int(event.get("value", iteration))
            elif event_type == "errors":
                error_count = int(event.get("value", error_count))
            elif event_type == "tokens":
                token_usage = int(event.get("value", token_usage))
            elif event_type == "llm/response":
                raw = event.get("response")
                last_response = LLMResponse.from_dict(raw) if raw else None
            elif event_type == "tool/dispatch":
                operation_recovery.update(event.get("operation_recovery") or {})
                calls = [ToolCall.from_dict(item) for item in event.get("tool_calls") or []]
                try:
                    validate_tool_call_ids(calls)
                except ValueError as error:
                    raise RecoveryMismatchError(str(error)) from error
                for tc in calls:
                    results.pop(tc.id, None)
                    dispatched[tc.id] = tc
            elif event_type == "tool/result":
                tc = ToolCall.from_dict(event["tool_call"])
                results[tc.id] = (tc, ToolResult.from_dict(event["result"]))
            elif event_type == "tool/collect":
                for tc_id in event.get("tool_call_ids") or []:
                    pair = results.get(tc_id)
                    if pair is None:
                        logger.warning(
                            "session_journal_collect_without_result",
                            session_id=session_id,
                            tool_call_id=tc_id,
                        )
                        continue
                    all_tool_results.append({"tool_call": pair[0], "result": pair[1]})
                    dispatched.pop(tc_id, None)
            elif event_type == "state":
                try:
                    state = SessionState(str(event.get("value")))
                except ValueError:
                    logger.warning(
                        "session_journal_bad_state",
                        session_id=session_id,
                        value=event.get("value"),
                    )
            elif event_type == "session/end":
                completed = True
                end_reason = str(event.get("reason", "unknown"))

        if start is None:
            logger.warning("session_journal_no_start", session_id=session_id)
            return None

        if self._verify_invocation is not None:
            await self._verify_invocation(dict(start.get("invocation") or {}))
        if self._verify_root is not None:
            if not start.get("build_id"):
                raise RecoveryMismatchError("Session runtime build is missing or changed")
            build_id = await self.verify_root(session_id)
            if start["build_id"] != build_id:
                raise RecoveryMismatchError("Session runtime build is missing or changed")
        pending_collect: list[tuple[ToolCall, ToolResult]] = []
        for tc_id, tc in dispatched.items():
            pair = results.get(tc_id)
            if pair is not None:
                pending_collect.append(pair)
                continue
            if self.resolve_dispatch is None:
                raise ValueError("Dispatch recovery requires an invocation resolver")
            recovered = await self.resolve_dispatch(
                session_id, tc, operation_recovery.get(tc_id, {})
            )
            await self.append(
                session_id, "tool/result", tool_call=tc.to_dict(), result=recovered.to_dict()
            )
            pending_collect.append((tc, recovered))

        if not completed and state == SessionState.WAITING and not pending_collect:
            # Crash window between the tool/collect append and the state
            # append in _apply_outcome: nothing is actually pending, so
            # resume as ACTIVE instead of waiting forever.
            logger.warning("session_journal_waiting_without_pending", session_id=session_id)
            state = SessionState.ACTIVE

        return SessionReplay(
            session_id=str(start.get("session_id") or session_id),
            agent_id=str(start.get("agent_id") or self._agent_id),
            trigger_content=str(start.get("trigger_content") or ""),
            carrier=start.get("carrier"),
            recipe_source=str(start.get("recipe") or ""),
            source_mail_id=start.get("source_mail_id"),
            invocation=dict(start.get("invocation") or {}),
            # Responsibility references remain independent from read state.
            responsibility=[str(mail_id) for mail_id in start.get("responsibility") or []],
            context=context,
            anchor_len=anchor_len,
            iteration=iteration,
            error_count=error_count,
            token_usage=token_usage,
            last_llm_response=last_response,
            all_tool_results=all_tool_results,
            loop_scratch=scratch,
            state=state,
            pending_collect=pending_collect,
            completed=completed,
            end_reason=end_reason,
        )

    async def derive_context(self, session_id: str) -> list[dict[str, Any]]:
        """Re-derive the session context from the log (test assertions)."""
        context: list[dict[str, Any]] = []
        for event in await self.read_events(session_id):
            event_type = event.get("type")
            if event_type == "session/start":
                context = list(event.get("context") or [])
            elif event_type == "context/append":
                context.extend(event.get("messages") or [])
            elif event_type == "context/compact":
                context = list(event.get("context") or [])
        return context

    async def end_reason(self, session_id: str) -> str | None:
        """The committed ``session/end`` reason, without reading events."""
        if session_id.startswith(PROGRAM_SOURCE_PREFIX):
            events = await self.read_events(session_id)
            return next((str(e["reason"]) for e in events if e["type"] == "session/end"), None)
        return await asyncio.to_thread(self._store.end_reason, session_id)

    async def list_completed(self, started_after: datetime | None = None) -> list[str]:
        """Completed session ids, ordered by completion, optionally after a watermark.

        Legacy sessions filter by their start clock; program entries filter by
        their terminal clock so late completion is visible. Undated records
        always pass. Only lifecycle metadata is read.
        """
        legacy = await asyncio.to_thread(self._store.completed_metadata, started_after)
        records = await asyncio.to_thread(self._program_metadata)
        completed = [(source_id, ended_at) for source_id, ended_at in legacy]
        completed.extend(
            (PROGRAM_SOURCE_PREFIX + record["run_id"], record["ended_at"])
            for record in records
            if (record["completed"] or record["error"] is not None)
            and (
                started_after is None
                or record["ended_at"] is None
                or datetime.fromisoformat(record["ended_at"]) > started_after
            )
        )
        return [
            source_id
            for source_id, _ in sorted(
                completed, key=lambda item: (_completion_time(item[1]), item[0])
            )
        ]

    async def count_completed(self, started_after: datetime | None = None) -> int:
        """Count completed sessions with the same metadata watermark filter."""
        return len(await self.list_completed(started_after))

    async def latest_completed(self) -> str | None:
        """The latest session by committed end metadata, or None.

        Feeds the context builder's ``thread-recap`` section (ADR 0028).
        Only lifecycle metadata is queried; event bodies are not read.
        """
        legacy = await asyncio.to_thread(self._store.latest_completed_metadata)
        candidates: list[tuple[str, str]] = []
        if legacy is not None:
            candidates.append((legacy[1], legacy[0]))
        for record in await asyncio.to_thread(self._program_metadata):
            if record["completed"] or record["error"] is not None:
                candidates.append(
                    (record["ended_at"] or "", PROGRAM_SOURCE_PREFIX + record["run_id"])
                )
        return (
            max(candidates, key=lambda item: (_completion_time(item[0]), item[1]))[1]
            if candidates
            else None
        )

    async def find_unfinished(self) -> list[str]:
        """Started sessions without a committed end, in session id order.

        Boot recovery (ADR 0007 + ADR 0028) resumes the first id. Stray
        events without ``session/start`` do not participate, and completed
        session events are never read by this metadata query.
        """
        return await asyncio.to_thread(self._store.unfinished_ids)

    async def read_events(self, session_id: str) -> list[dict[str, Any]]:
        """Read only the requested session's events, ordered by sequence."""
        if session_id.startswith(PROGRAM_SOURCE_PREFIX):
            run_id = session_id.removeprefix(PROGRAM_SOURCE_PREFIX)
            records = await asyncio.to_thread(self._program_history, run_id)
            return next(
                (program_events(r, self._agent_id) for r in records if r["run"].run_id == run_id),
                [],
            )
        return await asyncio.to_thread(self._store.events, session_id)

    def _program_metadata(self) -> list[dict[str, Any]]:
        if self._program_store is None:
            if not self._program_database.exists():
                return []
            self._program_store = ProgramStore(self._program_database)
        return self._program_store.entry_metadata()

    def _program_history(
        self, run_id: str | None = None, *, include_invocations: bool = True
    ) -> list[dict[str, Any]]:
        if self._program_store is None:
            if not self._program_database.exists():
                return []
            self._program_store = ProgramStore(self._program_database)
        return self._program_store.entry_history(run_id, include_invocations=include_invocations)

    async def export_jsonl(self, directory: Path) -> None:
        """Export reading artifacts including pending entries; never resume work."""
        await asyncio.to_thread(self._store.export_jsonl, directory)
        records = await asyncio.to_thread(self._program_history)

        def export() -> None:
            for record in records:
                source_id = PROGRAM_SOURCE_PREFIX + record["run"].run_id
                directory.mkdir(parents=True, exist_ok=True)
                destination = directory / (quote(source_id, safe="") + ".jsonl")
                destination.write_text(
                    "".join(
                        json.dumps(e, ensure_ascii=False) + "\n"
                        for e in program_events(record, self._agent_id)
                    ),
                    encoding="utf-8",
                )

        await asyncio.to_thread(export)
