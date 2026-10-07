"""SessionJournal — append-only per-session event log (ADR 0007).

Every cognitive session gets one JSONL event log at
``~/.tlon/{agent_id}/sessions/{session_id}.jsonl``. The log is the sole
source of truth for session state: the engine appends every state change
*before* mutating the in-memory :class:`~tlon.agents.session.AgentSession`
(the invariant "model-visible ⇒ journaled"), and a restart rebuilds the
session by replaying the log.

One event per line: ``{seq, ts, type, ...payload}`` — ``seq`` is a
per-file increasing sequence number, ``ts`` an ISO 8601 timestamp. Event
types: ``session/start``, ``context/append``, ``context/compact``,
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
- Orphaned dispatches (a ``tool/dispatch`` with no matching
  ``tool/collect``): a journaled ``tool/result`` is kept; a dispatched
  call without a result gets a synthetic
  ``ToolResult(success=False, error="interrupted: process restart")``
  which is also appended to the log (self-healing). Together they are
  ``pending_collect`` — the input of the first collect after resume.
- A log containing ``session/end`` is completed: the scheduler only
  marks the mail read, never re-runs the session.

The read side tolerates corrupt lines (a crash can leave a torn tail
line): each line is parsed independently and a bad line is skipped with
a warning, same as the mailbox reader. Replay never re-executes side
effects — mails and on-complete actions happened (or did not) in the
previous process; replay only rebuilds data fields. Completed logs
(``list_completed``) also feed the journal → claims distillation
trigger (ADR 0017): the log is the sole interaction truth, so
distillation reads it directly.

Writes are serialized by one asyncio lock per journal file, shared
process-wide across SessionJournal instances (same pattern as the
mailbox). A failed append raises — the engine then refuses to mutate the
in-memory state, keeping the log and the session consistent.
"""

import asyncio
import json
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiofiles

from tlon.agents.session import SessionState
from tlon.base.llm_client import LLMResponse, ToolCall
from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths
from tlon.tools.base import ToolResult

logger = get_logger(__name__)

#: Error text of the synthetic ToolResult replay fabricates for a tool
#: call that was dispatched but never completed (process restart).
INTERRUPTED_ERROR = "interrupted: process restart"

#: Bound on the shared lock table (same magnitude as the mailbox's).
_LOCKS_CAP = 4096


def session_id_for(agent_id: str, mail_id: str) -> str:
    """The session id of one trigger mail (one mail = one session).

    Single source for the scheduler's sid construction; the context
    builder's ``thread-recap`` section uses it to map a thread's earlier
    mails back to their session logs.
    """
    return f"{agent_id}-{mail_id}"


#: File-keyed locks shared by every SessionJournal over the same file
#: (bounded LRU; a locked entry is never evicted — see mailbox._lock_for).
_LOCKS: OrderedDict[Path, asyncio.Lock] = OrderedDict()


def _lock_for(path: Path) -> asyncio.Lock:
    """Return the shared lock for a journal file (bounded LRU)."""
    lock = _LOCKS.get(path)
    if lock is not None:
        _LOCKS.move_to_end(path)
        return lock
    lock = asyncio.Lock()
    _LOCKS[path] = lock
    if len(_LOCKS) > _LOCKS_CAP:
        for candidate_path, candidate in _LOCKS.items():
            if candidate_path == path:
                continue  # never evict the lock we are about to return
            if not candidate.locked():
                del _LOCKS[candidate_path]
                break
    return lock


@dataclass
class SessionReplay:
    """The session state rebuilt from one journal (``resume`` result)."""

    session_id: str
    agent_id: str
    mail_id: str
    trigger_content: str
    carrier: dict[str, str | None] | None
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

    The journal knows nothing about session semantics beyond the event
    vocabulary above; the engine (LoopDriver / SessionScheduler) decides
    what to write and when.
    """

    def __init__(self, agent_id: str, tlon_paths: TlonPaths):
        """Initialize the journal for one agent.

        Args:
            agent_id: Owning agent id (the sessions dir lives under its home)
            tlon_paths: Path resolver (tests inject an isolated home)
        """
        self._agent_id = agent_id
        self._paths = tlon_paths
        # Per-session next sequence number, counted from the file on first
        # append after process start. Mutated only under the file lock.
        self._seq: dict[str, int] = {}

    def _path(self, session_id: str) -> Path:
        return self._paths.agent_sessions_dir(self._agent_id) / f"{session_id}.jsonl"

    async def append(self, session_id: str, event_type: str, **payload: Any) -> None:
        """Append one event to the session's log.

        Raises on I/O failure: the caller must NOT mutate the in-memory
        session when the journal write failed (the log is the truth).

        Args:
            session_id: Session the event belongs to
            event_type: One of the event types in the module docstring
            payload: JSON-serializable event payload
        """
        path = self._path(session_id)
        async with _lock_for(path):
            seq = await self._next_seq(session_id, path)
            event = {
                "seq": seq,
                "ts": datetime.now(UTC).isoformat(),
                "type": event_type,
                "session_id": session_id,
                **payload,
            }
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                async with aiofiles.open(path, "a", encoding="utf-8") as f:
                    await f.write(json.dumps(event, ensure_ascii=False) + "\n")
            except Exception as e:
                logger.error(
                    "session_journal_append_failed",
                    session_id=session_id,
                    event_type=event_type,
                    file_path=str(path),
                    error=str(e),
                    exc_info=True,
                )
                raise
            self._seq[session_id] = seq + 1

    async def _next_seq(self, session_id: str, path: Path) -> int:
        """The next sequence number for a session (file line count on first use)."""
        if session_id not in self._seq:
            if path.exists():
                try:
                    async with aiofiles.open(path, encoding="utf-8") as f:
                        content = await f.read()
                    self._seq[session_id] = sum(1 for line in content.splitlines() if line.strip())
                except Exception as e:
                    logger.error("session_journal_read_failed", file_path=str(path), error=str(e))
                    self._seq[session_id] = 0
            else:
                self._seq[session_id] = 0
        return self._seq[session_id]

    async def resume(self, session_id: str) -> SessionReplay | None:
        """Replay a session's log, rebuilding its state.

        Returns None when there is no log (or no ``session/start`` in it)
        — the caller proceeds as for a fresh session. Orphaned tool
        dispatches are healed: a synthetic interrupted result is appended
        for each dispatched call that never produced a result.
        """
        events = await self.read_events(session_id)
        if not events:
            return None

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
                for tc_dict in event.get("tool_calls") or []:
                    tc = ToolCall.from_dict(tc_dict)
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

        # Orphan dispatches (dispatch without collect): keep journaled
        # results; synthesize an interrupted result for the rest — and
        # persist it, so a second resume sees a healed log.
        pending_collect: list[tuple[ToolCall, ToolResult]] = []
        for tc_id, tc in dispatched.items():
            pair = results.get(tc_id)
            if pair is not None:
                pending_collect.append(pair)
                continue
            synthetic = ToolResult(success=False, data={}, error=INTERRUPTED_ERROR)
            logger.warning(
                "session_journal_orphan_tool",
                session_id=session_id,
                tool_call_id=tc_id,
                tool_name=tc.function_name,
            )
            await self.append(
                session_id, "tool/result", tool_call=tc.to_dict(), result=synthetic.to_dict()
            )
            pending_collect.append((tc, synthetic))

        if not completed and state == SessionState.WAITING and not pending_collect:
            # Crash window between the tool/collect append and the state
            # append in _apply_outcome: nothing is actually pending, so
            # resume as ACTIVE instead of waiting forever.
            logger.warning("session_journal_waiting_without_pending", session_id=session_id)
            state = SessionState.ACTIVE

        return SessionReplay(
            session_id=str(start.get("session_id") or session_id),
            agent_id=str(start.get("agent_id") or self._agent_id),
            mail_id=str(start.get("mail_id") or ""),
            trigger_content=str(start.get("trigger_content") or ""),
            carrier=start.get("carrier"),
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
        """The ``session/end`` reason if the log has one, else None."""
        for event in reversed(await self.read_events(session_id)):
            if event.get("type") == "session/end":
                return str(event.get("reason", "unknown"))
        return None

    async def list_completed(self) -> list[str]:
        """Session ids whose log carries a ``session/end``.

        The distillation trigger (``MemoryManager``) diffs this set
        against the persisted watermark (a scalar timestamp — session
        ids are uuids without temporal order), so a session is
        distilled exactly once. Order is stable (file name order).
        """
        sessions_dir = self._paths.agent_sessions_dir(self._agent_id)
        completed: list[str] = []
        for path in sorted(sessions_dir.glob("*.jsonl")):
            if await self.end_reason(path.stem) is not None:
                completed.append(path.stem)
        return completed

    async def read_events(self, session_id: str) -> list[dict[str, Any]]:
        """Read all events from the log, skipping malformed lines."""
        path = self._path(session_id)
        if not path.exists():
            return []
        try:
            async with aiofiles.open(path, encoding="utf-8") as f:
                content = await f.read()
        except Exception as e:
            logger.error("session_journal_read_failed", file_path=str(path), error=str(e))
            return []

        events: list[dict[str, Any]] = []
        for line_num, line in enumerate(content.splitlines()):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
                if isinstance(event, dict):
                    events.append(event)
            except json.JSONDecodeError as e:
                logger.warning(
                    "session_journal_parse_failed",
                    file_path=str(path),
                    line_number=line_num,
                    error=str(e),
                )
        return events
