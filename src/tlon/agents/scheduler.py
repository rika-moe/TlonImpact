"""One member's serial session executor and background maintenance."""

import asyncio
import contextlib
import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from functools import partial
from typing import Any, TypeVar

from tlon.agents.loop_driver import LoopDriver
from tlon.agents.member_spec import SessionRecipe
from tlon.agents.recovery import blocked_reason, blocks_recovery
from tlon.agents.session import AgentSession, SessionState
from tlon.agents.session_journal import SessionJournal, SessionReplay
from tlon.base.config import AgentConfig
from tlon.base.execution import (
    ExecutionScope,
    UnknownModelOutcomeError,
    bind_execution,
    current_execution,
)
from tlon.base.llm_client import ToolCall
from tlon.base.logger import get_logger
from tlon.memory.manager import MemoryManager
from tlon.operations.schema import UnknownOutcomeError
from tlon.organization.mail.mail import Mail
from tlon.organization.mail.mailbox import Mailbox
from tlon.tools.base import ToolResult
from tlosure.edn import dumps, loads

logger = get_logger(__name__)
SessionSpan = Callable[[AgentSession], AbstractContextManager[None]]
CheckReflection = Callable[[], Awaitable[None]]
NoteInteraction = Callable[[], None]
_Result = TypeVar("_Result")


class SessionScheduler:
    """Run explicit session invocations, never infer a program from mail."""

    def __init__(
        self,
        config: AgentConfig,
        memory: MemoryManager,
        session_span: SessionSpan,
        check_reflection: CheckReflection,
        note_interaction: NoteInteraction,
        make_driver: Callable[[SessionRecipe], LoopDriver],
        journal: SessionJournal,
        mailbox: Mailbox | None,
        can_schedule: Callable[[], bool] | None = None,
        state_changed: Callable[[], Awaitable[None]] | None = None,
        apply_scheduling_request: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        resume_invocation: Callable[[AgentSession], Awaitable[None]] | None = None,
        needs_continuation: Callable[[dict[str, Any]], Awaitable[bool]] | None = None,
        verify_invocation: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        block_invocation: Callable[[dict[str, Any], str], Awaitable[None]] | None = None,
        execution_factory: Callable[[str, SessionRecipe], Awaitable[ExecutionScope]] | None = None,
        maintenance_factory: Callable[[str], ExecutionScope] | None = None,
    ) -> None:
        self._config = config
        self._id = config.id
        self._memory = memory
        self._session_span = session_span
        self._check_reflection = check_reflection
        self._note_interaction = note_interaction
        self._make_driver = make_driver
        self._journal = journal
        self._mailbox = mailbox
        self._can_schedule = can_schedule or (lambda: True)
        self._state_changed = state_changed
        self._apply_scheduling_request = apply_scheduling_request
        self._resume_invocation = resume_invocation
        self._block_invocation = block_invocation
        self._verify_invocation = verify_invocation
        self._needs_continuation = needs_continuation
        self._execution_factory = execution_factory
        self._maintenance_factory = maintenance_factory
        self._execution_scope: ExecutionScope | None = None
        self._session: AgentSession | None = None
        self._loop_driver: LoopDriver | None = None
        self._maintenance_running = False
        self._last_activity_at = datetime.now(UTC)
        self._worker_task: asyncio.Task[None] | None = None
        self.ready = asyncio.Event()
        self.recovery_ready = asyncio.Event()

    def start(self) -> None:
        self._worker_task = asyncio.create_task(self.run())

    async def wait_ready(self) -> None:
        """Wait for scheduler startup; recovery continues in the background."""
        await self.ready.wait()
        if self._worker_task is not None and self._worker_task.done():
            self._worker_task.result()

    async def wait_recovered(self) -> None:
        """Wait until session recovery finishes before executing member programs."""
        await self.recovery_ready.wait()
        if self._worker_task is not None and self._worker_task.done():
            self._worker_task.result()

    async def stop(self) -> None:
        if self._execution_scope is not None:
            self._execution_scope.cancel()
        if self._session is not None:
            await self._session.cancel_pending()
        if self._worker_task is not None:
            self._worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker_task

    @property
    def active_session_count(self) -> int:
        return int(self._session is not None) + int(self._maintenance_running)

    @property
    def last_activity_at(self) -> datetime:
        if self._session is not None:
            return max(self._last_activity_at, self._session.last_active_at)
        return self._last_activity_at

    @property
    def idle_seconds(self) -> float:
        return (datetime.now(UTC) - self.last_activity_at).total_seconds()

    async def run(self) -> None:
        self.ready.set()
        recovered: set[str] = set()
        try:
            for sid in await self._journal.find_unfinished():
                if await self._recover_session(sid):
                    recovered.add(sid)
            # A committed session/end may precede its program effect result.
            # Recover each effect independently, including non-mail programs;
            # two run-loop calls in one program are distinct continuations.
            if self._resume_invocation is not None:
                continuations: dict[tuple[str, int], tuple[str, SessionReplay]] = {}
                for sid in await self._journal.list_completed():
                    if sid in recovered:
                        continue
                    events = await self._journal.read_events(sid)
                    start = next(
                        (event for event in events if event["type"] == "session/start"), None
                    )
                    if start is None:
                        continue
                    invocation = start.get("invocation") or {}
                    effect = invocation.get("program_effect")
                    if effect is None:
                        continue
                    try:
                        if (
                            self._needs_continuation is not None
                            and not await self._needs_continuation(invocation)
                        ):
                            continue
                    except Exception as error:
                        if not blocks_recovery(error):
                            raise
                        await self._block_recovery(sid, error)
                        continue
                    key = (effect["run_id"], effect["index"])
                    mail_id = start.get("source_mail_id")
                    rule_name = invocation.get("rule_name")
                    if mail_id and rule_name:
                        dispatch = await self._journal.read_events(
                            f"dispatch:{mail_id}:{rule_name}"
                        )
                        if any(event["type"] == "dispatch/complete" for event in dispatch):
                            continue
                    stamp = str(start["ts"])
                    previous = continuations.get(key)
                    if previous is not None and previous[0] >= stamp:
                        continue
                    try:
                        replay = await self._retry_safe_recovery(
                            sid, partial(self._journal.resume, sid)
                        )
                    except Exception as error:
                        if not blocks_recovery(error):
                            raise
                        await self._block_recovery(sid, error)
                        continue
                    if replay is not None:
                        continuations[key] = (stamp, replay)
                for _, replay in sorted(continuations.values(), key=lambda item: item[0]):
                    try:
                        await self._resume_invocation(await self._restore_session(replay))
                    except Exception as error:
                        if not blocks_recovery(error):
                            raise
                        await self._block_recovery(replay.session_id, error)
        finally:
            self.recovery_ready.set()
        while True:
            if self._can_schedule() and self._session is None:
                try:
                    await self._check_reflection()
                except Exception as error:
                    logger.error("member_reflection_failed", error=str(error), exc_info=True)
            await asyncio.sleep(0.5)

    async def _recover_session(self, session_id: str) -> bool:
        async def recover() -> None:
            replay = await self._journal.resume(session_id)
            if replay is None:
                raise RuntimeError("unfinished session has no journal start")
            session = await self._restore_session(replay)
            recipe = SessionRecipe.from_edn(loads(session.recipe_source))
            await self._drive(session, recipe, replay.pending_collect or None)
            if self._resume_invocation is not None:
                await self._resume_invocation(session)

        try:
            await self._retry_safe_recovery(session_id, recover)
            return True
        except Exception as error:
            if not blocks_recovery(error):
                raise
            await self._block_recovery(session_id, error)
            return False

    async def _retry_safe_recovery(
        self, session_id: str, action: Callable[[], Awaitable[_Result]]
    ) -> _Result:
        """Both recovery paths hold the same startup barrier for retryable unknown."""
        delay = 0.5
        retry_logged = False
        while True:
            try:
                return await action()
            except UnknownOutcomeError as error:
                if error.data.get("retry_safe") is not True:
                    raise
                if not retry_logged:
                    logger.warning(
                        "session_recovery_waiting", session_id=session_id, error=str(error)
                    )
                    retry_logged = True
                # No newer member writes can cross an unresolved old root.
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)

    async def _block_recovery(self, session_id: str, error: BaseException) -> None:
        """Quarantine lifecycle only; preserve unknown invocation and model receipts."""
        events = await self._journal.read_events(session_id)
        start = next((event for event in events if event["type"] == "session/start"), None)
        reason = blocked_reason(error)
        # Parent first: if either write fails, startup must stop rather than
        # allow a quarantined continuation to resume after newer state writes.
        if start is not None and self._block_invocation is not None:
            await self._block_invocation(dict(start.get("invocation") or {}), reason)
        if await self._journal.end_reason(session_id) != "recovery_blocked":
            await self._journal.append(session_id, "session/recovery-blocked", error=reason)
            logger.error("session_recovery_blocked", session_id=session_id, error=reason)
        self._session = None

    async def run_invocation(
        self,
        recipe: SessionRecipe,
        trigger: str,
        responsibility: list[Mail],
        carrier: dict[str, str | None] | None,
        invocation: dict[str, Any],
    ) -> None:
        effect = invocation.get("program_effect")
        if effect is not None:
            key = json.dumps([effect["run_id"], effect["index"]], separators=(",", ":"))
            session_id = f"{self._id}-program-{hashlib.sha256(key.encode()).hexdigest()[:24]}"
            try:
                replay = await self._journal.resume(session_id)
            except Exception as error:
                if blocks_recovery(error):
                    await self._block_recovery(session_id, error)
                raise
            if replay is not None:
                if replay.completed:
                    return
                frozen = SessionRecipe.from_edn(loads(replay.recipe_source))
                await self._drive(
                    await self._restore_session(replay), frozen, replay.pending_collect or None
                )
                return
        else:
            session_id = f"{self._id}-{uuid.uuid4().hex[:12]}"
        session = AgentSession(
            session_id,
            trigger,
            carrier=carrier,
            responsibility=responsibility,
            recipe_source=dumps(recipe.to_edn_data()),
            source_mail_id=responsibility[0].id if responsibility else None,
            invocation=invocation,
        )
        await self._drive(session, recipe)

    async def _drive(
        self,
        session: AgentSession,
        recipe: SessionRecipe,
        pending_collect: list[tuple[ToolCall, ToolResult]] | None = None,
    ) -> None:
        scope: ExecutionScope | None = None
        try:
            scope = (
                await self._execution_factory(session.id, recipe)
                if self._execution_factory
                else current_execution()
            )
            self._execution_scope = scope
            if self._verify_invocation is not None:
                await self._verify_invocation(session.invocation)
            with bind_execution(scope):
                await self._drive_scoped(session, recipe, pending_collect)
        except Exception as error:
            if blocks_recovery(error):
                await self._block_recovery(session.id, error)
            raise
        finally:
            if scope is not None and self._execution_factory is not None:
                scope.cancel()
            self._execution_scope = None

    async def _drive_scoped(
        self,
        session: AgentSession,
        recipe: SessionRecipe,
        pending_collect: list[tuple[ToolCall, ToolResult]] | None = None,
    ) -> None:
        self._loop_driver = self._make_driver(recipe)
        self._session = session
        try:
            while not session.is_completed:
                if self._state_changed is not None:
                    await self._state_changed()
                if self._is_session_timed_out(session):
                    await self._end_session(session, "timeout")
                    await self._notify_failure_reply(session, "timeout")
                    break
                try:
                    with self._session_span(session):
                        if pending_collect:
                            await self._loop_driver.resume_collect(session, pending_collect)
                            pending_collect = None
                        elif session.state is SessionState.WAITING:
                            await self._loop_driver.collect_session_results(session)
                        else:
                            await self._loop_driver.step_session(session)
                except UnknownModelOutcomeError as error:
                    raise UnknownOutcomeError(
                        str(error),
                        {
                            "call_id": session.id,
                            "outcome": "unknown",
                            "retry_safe": False,
                            "unknown_source": "model",
                        },
                    ) from error
                except UnknownOutcomeError:
                    raise
                except Exception as error:
                    logger.error(
                        "session_error", session_id=session.id, error=str(error), exc_info=True
                    )
                    await self._end_session(session, "error")
                    await self._notify_failure_reply(session, "error")
                await asyncio.sleep(0.01)
            await self._ensure_session_end(session)
            self._note_interaction()
            if self._can_schedule():
                self._maintenance_running = True
                try:
                    await self._check_session_distillation()
                finally:
                    self._maintenance_running = False
        finally:
            await session.cancel_pending()
            self._session = None
            self._last_activity_at = datetime.now(UTC)
            request = session.loop_scratch.get("scheduling_request")
            if request is not None and self._apply_scheduling_request is not None:
                await self._apply_scheduling_request(request)
            if self._state_changed is not None:
                await self._state_changed()

    async def _restore_session(self, replay: SessionReplay) -> AgentSession:
        """Rebuild the in-memory session from a journal replay.

        Data fields only — side effects (mail effects) are
        never re-executed (ADR 0007). The responsibility id list is
        resolved back to Mail objects through the mailbox; an id whose
        mail is gone is dropped (the set is only used for failure-reply
        anchoring and program continuation).
        """
        responsibility: list[Mail] = []
        if self._mailbox is not None:
            for mail_id in replay.responsibility:
                mail = await self._mailbox.find(mail_id)
                if mail is not None:
                    responsibility.append(mail)
        session = AgentSession(
            replay.session_id,
            replay.trigger_content,
            carrier=replay.carrier,
            responsibility=responsibility,
            recipe_source=replay.recipe_source,
            source_mail_id=replay.source_mail_id,
            invocation=replay.invocation,
        )
        session.context = replay.context
        session.anchor_len = replay.anchor_len
        session._iteration = replay.iteration
        session._error_count = replay.error_count
        session._token_usage = replay.token_usage
        session._last_llm_response = replay.last_llm_response
        session._all_tool_results = replay.all_tool_results
        session.loop_scratch = replay.loop_scratch
        session.state = replay.state
        return session

    async def _notify_failure_reply(self, session: AgentSession, reason: str) -> None:
        """Best-effort failure notification when a session ends abnormally
        — one mail per human sender in the responsibility set (ADR 0027 as
        revised by ADR 0028 决策 7); agent senders are logged, not answered.

        Never raises: the reply is courtesy; a failure here must not
        break the scheduler loop.
        """
        try:
            assert self._loop_driver is not None
            await self._loop_driver.send_failure_reply(session, reason)
        except Exception as e:
            logger.error("session_failure_reply_failed", session_id=session.id, error=str(e))

    async def _end_session(self, session: AgentSession, reason: str) -> None:
        """Journal ``session/end``, then mark the session completed.

        A journal failure must not wedge the session: the reap proceeds
        and the session is re-run on the next boot.
        """
        try:
            await self._journal.append(session.id, "session/end", reason=reason)
        except Exception as e:
            logger.error("session_journal_end_failed", session_id=session.id, error=str(e))
        session.mark_completed()

    async def _ensure_session_end(self, session: AgentSession) -> None:
        """Backstop: every reaped session must carry a ``session/end``.

        The loop driver journals the end on its own completion paths;
        this covers any completion that bypassed them. Idempotent — a
        log that already ends is left alone.
        """
        try:
            if await self._journal.end_reason(session.id) is None:
                await self._journal.append(session.id, "session/end", reason="complete")
        except Exception as e:
            logger.error("session_journal_end_failed", session_id=session.id, error=str(e))

    async def _check_session_distillation(self) -> None:
        """Run the batch distillation trigger at the reap point (ADR 0017).

        Never raises: a distillation failure (LLM outage, disk error)
        is logged and retried at the next session's reap — the pending
        sessions stay pending because the watermark never advanced.
        """
        try:
            scope = (
                self._maintenance_factory("distill")
                if self._maintenance_factory
                else current_execution()
            )
            with bind_execution(scope):
                claim_count = await self._memory.maybe_distill_completed_sessions(self._journal)
        except Exception as e:
            logger.error("session_distillation_check_failed", error=str(e), exc_info=True)
            return
        if claim_count:
            logger.info("session_distillation_completed", claim_count=claim_count)

    def _is_session_timed_out(self, session: AgentSession) -> bool:
        """A session idle beyond the configured timeout is considered stuck."""
        timeout = self._config.session_timeout_seconds
        idle = (datetime.now(UTC) - session.last_active_at).total_seconds()
        return idle > timeout
