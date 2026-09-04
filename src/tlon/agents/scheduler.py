"""Session scheduler for Agent.

Owns the agent's continuous main loop: drains the message queue into
sessions, advances each session (ReAct step or tool-result collection),
and reaps completed/stale sessions. The agent-level state derives on
read (Agent.state property), never by callback from here. Extracted from
the Agent god class.

The whole loop task runs inside bound_contextvars(agent_id=...) so all
logs emitted from session advances carry the agent binding; tasks spawned
from here (e.g. reflection) inherit the context.

P4/C2 follow-up: the scheduler runs on injected capabilities
(config, memory, trace/mail/reflection delegates) and advances sessions
via the directly injected LoopDriver — no back-reference to the Agent at
all (the last host protocol retired here). The mail queue and the worker
lifecycle (``_worker_running`` / ``_worker_task``) are owned here (R2):
the scheduler drives them, so they live here.
"""

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from contextlib import AbstractContextManager
from datetime import datetime

from structlog.contextvars import bound_contextvars

from tlon.agents.loop_driver import LoopDriver
from tlon.agents.session import AgentSession, SessionState
from tlon.agents.session_journal import SessionJournal, SessionReplay
from tlon.base.config import AgentConfig
from tlon.base.llm_client import ToolCall
from tlon.base.logger import get_logger
from tlon.memory.manager import MemoryManager
from tlon.organization.mail.mail import Mail
from tlon.tools.base import ToolResult

logger = get_logger(__name__)

# Injected delegate signatures (assembled from StageServices /
# EffectExecutor / ReflectionEngine at Agent construction).
SessionSpan = Callable[[AgentSession], AbstractContextManager[None]]
MarkMailRead = Callable[["Mail | None"], Awaitable[None]]
CheckReflection = Callable[[], Awaitable[None]]
NoteInteraction = Callable[[], None]


class SessionScheduler:
    """Multi-session scheduling loop for one agent."""

    def __init__(
        self,
        config: AgentConfig,
        memory: MemoryManager,
        session_span: SessionSpan,
        mark_mail_read: MarkMailRead,
        check_reflection: CheckReflection,
        note_interaction: NoteInteraction,
        loop_driver: LoopDriver,
        journal: SessionJournal,
    ):
        self._id = config.id
        self._config = config
        self._memory = memory
        self._session_span = session_span
        self._mark_mail_read = mark_mail_read
        self._check_reflection = check_reflection
        self._note_interaction = note_interaction
        self._loop_driver = loop_driver
        self._journal = journal
        # Live (non-completed) sessions; run() rebinds this to its working
        # dict. Read by the registry's idle monitor via the properties below.
        self._sessions: dict[str, AgentSession] = {}
        # Floor for last activity: loop start time, bumped on session
        # creation and session completion (see run()).
        self._last_activity_at: datetime = datetime.now()
        # Inbound mail queue (bounded: a full queue drops new mail rather
        # than blocking the EventBus dispatch — see Agent._enqueue_mail)
        # and the worker lifecycle, both owned here (R2).
        self._mail_queue: asyncio.Queue[tuple[Mail, dict[str, str | None]]] = (
            asyncio.Queue(maxsize=config.message_queue_size)
        )
        self._worker_running = False
        self._worker_task: asyncio.Task[None] | None = None

    def enqueue(self, mail: Mail, carrier: dict[str, str | None]) -> bool:
        """Enqueue a mail for session processing.

        Returns:
            True when queued; False when the bounded queue is full (the
            caller logs the drop — the mail stays unread in the mailbox
            and is picked up again by the next boot scan)
        """
        try:
            self._mail_queue.put_nowait((mail, carrier))
        except asyncio.QueueFull:
            return False
        return True

    def start(self) -> None:
        """Start the main-loop worker task."""
        self._worker_running = True
        self._worker_task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        """Stop the main-loop worker task."""
        self._worker_running = False
        if self._worker_task:
            self._worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker_task

    @property
    def active_session_count(self) -> int:
        """Number of live sessions (ACTIVE or WAITING; completed ones are reaped)."""
        return len(self._sessions)

    @property
    def has_queue_capacity(self) -> bool:
        """The bounded mail queue can take at least one more mail.

        Read by the member runtime's deferred rescan to trickle boot-scan
        overflow in as sessions drain.
        """
        return not self._mail_queue.full()

    @property
    def last_activity_at(self) -> datetime:
        """Most recent session activity timestamp.

        "Activity" means: a session was created, a live session's
        ``last_active_at`` (updated on every state change/step), or a
        session completed. With no sessions at all, this is the loop start
        time — so a freshly started agent is considered recently active.
        """
        last = self._last_activity_at
        for session in self._sessions.values():
            if session.last_active_at > last:
                last = session.last_active_at
        return last

    @property
    def idle_seconds(self) -> float:
        """Seconds since the last session activity (see last_activity_at)."""
        return (datetime.now() - self.last_activity_at).total_seconds()

    async def run(self) -> None:
        """Agent's continuous main loop with multi-session scheduling.

        Cycles through all active sessions each pass:
        0. Reflection trigger (timer-driven, no mail needed)
        1. New messages -> create/resume sessions (journal replay, ADR 0007),
           at most ``config.max_concurrent_sessions`` live at once — excess
           mail waits in the bounded queue (backlog throttling, issue )
        2. ACTIVE sessions -> one think+dispatch step
        3. WAITING sessions -> collect completed background tools
        4. COMPLETED -> cleanup (+ reflection counter and the batch
           summary trigger, ADR 0009)
        """
        # This loop runs in its own task per agent, so binding agent_id for
        # its whole lifetime is safe and also covers logs inside session
        # advances; tasks spawned here (e.g. reflection) inherit the context.
        with bound_contextvars(agent_id=self._id):
            logger.info("agent_main_loop_started")
            sessions: dict[str, AgentSession] = {}
            session_mails: dict[str, Mail] = {}
            # Resumed sessions with journaled-but-uncollected tool results:
            # fed to LoopDriver.resume_collect once, then back to normal.
            pending_collects: dict[str, list[tuple[ToolCall, ToolResult]]] = {}
            self._sessions = sessions
            self._last_activity_at = datetime.now()

            while self._worker_running:
                # ── 0. Reflection trigger (timer-driven, no mail needed) ──
                await self._check_reflection()

                # ── 1. New mail — one session per mail, capped concurrency ──
                incoming: tuple[Mail, dict[str, str | None]] | None = None
                if len(sessions) < self._config.max_concurrent_sessions:
                    try:
                        incoming = self._mail_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        # No new mail — if no sessions either, block
                        if not sessions:
                            try:
                                incoming = await asyncio.wait_for(
                                    self._mail_queue.get(), timeout=0.5
                                )
                            except TimeoutError:
                                continue
                if incoming is not None:
                    mail, carrier = incoming
                    await self._admit(mail, carrier, sessions, session_mails, pending_collects)

                # ── 2 & 3. Advance sessions ──
                for sid in list(sessions.keys()):
                    session = sessions[sid]
                    if session.id not in session_mails:
                        continue

                    # Stale sessions (stuck ACTIVE/WAITING) are abandoned
                    if self._is_session_timed_out(session):
                        logger.warning(
                            "session_timeout",
                            session_id=sid,
                            state=session.state.value,
                            idle_seconds=int(
                                (datetime.now() - session.last_active_at).total_seconds()
                            ),
                        )
                        await self._end_session(session, "timeout")
                        await self._notify_failure_reply(
                            session, session_mails.get(sid), "timeout"
                        )
                    else:
                        try:
                            with self._session_span(session):
                                if sid in pending_collects:
                                    # Crash recovery (ADR 0007): harvest the
                                    # journaled tool results once, then the
                                    # session rejoins the normal flow.
                                    await self._loop_driver.resume_collect(
                                        session, pending_collects.pop(sid)
                                    )
                                elif session.state == SessionState.WAITING:
                                    await self._loop_driver.collect_session_results(session)
                                elif session.state == SessionState.ACTIVE:
                                    mail = session_mails[sid]
                                    await self._loop_driver.step_session(session, mail)
                        except Exception as e:
                            logger.error(
                                "session_error", session_id=sid, error=str(e), exc_info=True
                            )
                            await self._end_session(session, "error")
                            await self._notify_failure_reply(
                                session, session_mails.get(sid), "error"
                            )

                    # ── 4. Cleanup: the mail is processed, cursor advances ──
                    if session.is_completed:
                        await self._ensure_session_end(session)
                        await session.cancel_pending()
                        await self._mark_mail_read(session_mails.get(sid))
                        del sessions[sid]
                        session_mails.pop(sid, None)
                        pending_collects.pop(sid, None)
                        self._last_activity_at = datetime.now()
                        # Session completed: feed the reflection counter
                        # and check the batch summary trigger (ADR 0009 —
                        # journal -> episodic, threshold config.summary_window).
                        self._note_interaction()
                        await self._check_session_summaries()

                # Agent state derives on read (Agent.state property) — the
                # scheduler never calls back into the Agent.

                # Yield control
                await asyncio.sleep(0.01 if sessions else 0.05)

            logger.info("agent_main_loop_stopped")

    async def _admit(
        self,
        mail: Mail,
        carrier: dict[str, str | None],
        sessions: dict[str, AgentSession],
        session_mails: dict[str, Mail],
        pending_collects: dict[str, list[tuple[ToolCall, ToolResult]]],
    ) -> None:
        """Admit one mail as a session: resume from the journal when there
        is one (ADR 0007), else create a fresh session.

        A log with ``session/end`` means a previous process already ran
        this mail to completion: only the read cursor advances, the
        session is never re-run. A log with orphaned tool dispatches
        resumes with a one-shot ``resume_collect`` before the normal
        step/collect flow.
        """
        sid = f"{self._id}-{mail.id}"
        replay = await self._journal.resume(sid)
        if replay is not None and replay.completed:
            logger.info(
                "session_journal_completed", session_id=sid, end_reason=replay.end_reason
            )
            await self._mark_mail_read(mail)
            return
        if replay is None:
            sessions[sid] = AgentSession(sid, mail.content, carrier=carrier)
            logger.info("session_started", session_id=sid, content=mail.content)
        else:
            sessions[sid] = self._restore_session(replay, mail, carrier)
            logger.info(
                "session_resumed",
                session_id=sid,
                iteration=replay.iteration,
                pending_collect=len(replay.pending_collect),
            )
            if replay.pending_collect:
                pending_collects[sid] = replay.pending_collect
        session_mails[sid] = mail
        self._last_activity_at = datetime.now()

    def _restore_session(
        self, replay: SessionReplay, mail: Mail, carrier: dict[str, str | None]
    ) -> AgentSession:
        """Rebuild the in-memory session from a journal replay.

        Data fields only — side effects (mails, interaction saves,
        on-complete actions) are never re-executed (ADR 0007). The
        trigger mail and carrier are re-attached from the live delivery.
        """
        session = AgentSession(
            replay.session_id,
            replay.trigger_content,
            carrier=replay.carrier or carrier,
        )
        session.context = replay.context
        session.anchor_len = replay.anchor_len
        session._iteration = replay.iteration
        session._error_count = replay.error_count
        session._last_llm_response = replay.last_llm_response
        session._all_tool_results = replay.all_tool_results
        session.loop_scratch = replay.loop_scratch
        session.state = replay.state
        session.trigger_message = mail
        return session

    async def _notify_failure_reply(
        self, session: AgentSession, mail: Mail | None, reason: str
    ) -> None:
        """Best-effort reply to the trigger mail's sender when a session
        ends abnormally — the 'every mail gets an answer' contract.

        Never raises: the reply is courtesy; a failure here must not
        break the scheduler loop.
        """
        if mail is None:
            return
        try:
            await self._loop_driver.send_failure_reply(mail, reason)
        except Exception as e:
            logger.error(
                "session_failure_reply_failed", session_id=session.id, error=str(e)
            )

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

    async def _check_session_summaries(self) -> None:
        """Run the batch summary trigger at the reap point (ADR 0009).

        Never raises: a summarization failure (LLM outage, disk error)
        is logged and retried at the next session's reap — the pending
        sessions stay pending because no Summary records them.
        """
        try:
            summary_id = await self._memory.maybe_summarize_completed_sessions(self._journal)
        except Exception as e:
            logger.error("session_summary_check_failed", error=str(e), exc_info=True)
            return
        if summary_id:
            logger.info("session_summary_generated", summary_id=summary_id)

    def _is_session_timed_out(self, session: AgentSession) -> bool:
        """A session idle beyond the configured timeout is considered stuck."""
        timeout = self._config.session_timeout_seconds
        idle = (datetime.now() - session.last_active_at).total_seconds()
        return idle > timeout
