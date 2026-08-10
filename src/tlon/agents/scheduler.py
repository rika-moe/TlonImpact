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
from tlon.base.config import AgentConfig
from tlon.base.logger import get_logger
from tlon.memory.manager import MemoryManager
from tlon.organization.mail.mail import Mail

logger = get_logger(__name__)

# Injected delegate signatures (assembled from StageServices /
# EffectExecutor / ReflectionEngine at Agent construction).
SessionSpan = Callable[[AgentSession], AbstractContextManager[None]]
MarkMailRead = Callable[["Mail | None"], Awaitable[None]]
CheckReflection = Callable[[], Awaitable[None]]


class SessionScheduler:
    """Multi-session scheduling loop for one agent."""

    def __init__(
        self,
        config: AgentConfig,
        memory: MemoryManager,
        session_span: SessionSpan,
        mark_mail_read: MarkMailRead,
        check_reflection: CheckReflection,
        loop_driver: LoopDriver,
    ):
        self._id = config.id
        self._config = config
        self._memory = memory
        self._session_span = session_span
        self._mark_mail_read = mark_mail_read
        self._check_reflection = check_reflection
        self._loop_driver = loop_driver
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
        0. Check idle timeout + reflection trigger (proactive, no message needed)
        1. New messages -> create/resume sessions
        2. ACTIVE sessions -> one think+dispatch step
        3. WAITING sessions -> collect completed background tools
        4. COMPLETED -> cleanup
        """
        # This loop runs in its own task per agent, so binding agent_id for
        # its whole lifetime is safe and also covers logs inside session
        # advances; tasks spawned here (e.g. reflection) inherit the context.
        with bound_contextvars(agent_id=self._id):
            logger.info("agent_main_loop_started")
            sessions: dict[str, AgentSession] = {}
            session_mails: dict[str, Mail] = {}
            self._sessions = sessions
            self._last_activity_at = datetime.now()

            while self._worker_running:
                # ── 0. Proactive memory triggers (no mail required) ──
                await self._memory.check_idle_timeout()
                await self._check_reflection()

                # ── 1. New mail — one session per mail ──
                try:
                    mail, carrier = self._mail_queue.get_nowait()
                    sid = f"{self._id}-{mail.id}"
                    session = AgentSession(sid, mail.content, carrier=carrier)
                    sessions[sid] = session
                    session_mails[sid] = mail
                    self._last_activity_at = datetime.now()
                    logger.info("session_started", session_id=sid, content=mail.content)
                except asyncio.QueueEmpty:
                    # No new mail — if no sessions either, block
                    if not sessions:
                        try:
                            mail, carrier = await asyncio.wait_for(
                                self._mail_queue.get(), timeout=0.5
                            )
                            sid = f"{self._id}-{mail.id}"
                            sessions[sid] = AgentSession(sid, mail.content, carrier=carrier)
                            session_mails[sid] = mail
                            self._last_activity_at = datetime.now()
                        except TimeoutError:
                            continue

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
                        session.mark_completed()
                    else:
                        try:
                            with self._session_span(session):
                                if session.state == SessionState.WAITING:
                                    await self._loop_driver.collect_session_results(session)
                                elif session.state == SessionState.ACTIVE:
                                    mail = session_mails[sid]
                                    await self._loop_driver.step_session(session, mail)
                        except Exception as e:
                            logger.error(
                                "session_error", session_id=sid, error=str(e), exc_info=True
                            )
                            session.mark_completed()

                    # ── 4. Cleanup: the mail is processed, cursor advances ──
                    if session.is_completed:
                        await session.cancel_pending()
                        await self._mark_mail_read(session_mails.get(sid))
                        del sessions[sid]
                        session_mails.pop(sid, None)
                        self._last_activity_at = datetime.now()

                # Agent state derives on read (Agent.state property) — the
                # scheduler never calls back into the Agent.

                # Yield control
                await asyncio.sleep(0.01 if sessions else 0.05)

            logger.info("agent_main_loop_stopped")

    def _is_session_timed_out(self, session: AgentSession) -> bool:
        """A session idle beyond the configured timeout is considered stuck."""
        timeout = self._config.session_timeout_seconds
        idle = (datetime.now() - session.last_active_at).total_seconds()
        return idle > timeout
