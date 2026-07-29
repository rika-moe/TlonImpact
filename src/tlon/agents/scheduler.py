"""Session scheduler for Agent.

Owns the agent's continuous main loop: drains the message queue into
sessions, advances each session (ReAct step or tool-result collection),
reaps completed/stale sessions, and derives the agent-level state.
Extracted from the Agent god class.

The whole loop task runs inside bound_contextvars(agent_id=...) so all
logs emitted from session advances carry the agent binding; tasks spawned
from here (e.g. reflection) inherit the context.
"""

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING

from structlog.contextvars import bound_contextvars

from tlon.agents.session import AgentSession, SessionState
from tlon.base.logger import get_logger
from tlon.organization.mail.mail import Mail

if TYPE_CHECKING:
    from tlon.agents.agent import Agent

logger = get_logger(__name__)


class SessionScheduler:
    """Multi-session scheduling loop for one agent."""

    def __init__(self, agent: "Agent"):
        self._agent = agent
        # Live (non-completed) sessions; run() rebinds this to its working
        # dict. Read by the registry's idle monitor via the properties below.
        self._sessions: dict[str, AgentSession] = {}
        # Floor for last activity: loop start time, bumped on session
        # creation and session completion (see run()).
        self._last_activity_at: datetime = datetime.now()

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
        agent = self._agent
        # This loop runs in its own task per agent, so binding agent_id for
        # its whole lifetime is safe and also covers logs inside session
        # advances; tasks spawned here (e.g. reflection) inherit the context.
        with bound_contextvars(agent_id=agent.id):
            logger.info("agent_main_loop_started")
            sessions: dict[str, AgentSession] = {}
            session_mails: dict[str, Mail] = {}
            self._sessions = sessions
            self._last_activity_at = datetime.now()

            while agent._worker_running:
                # ── 0. Proactive memory triggers (no mail required) ──
                await agent.memory.check_idle_timeout()
                await agent._check_reflection_trigger()

                # ── 1. New mail — one session per mail ──
                try:
                    mail, carrier = agent._mail_queue.get_nowait()
                    sid = f"{agent.id}-{mail.id}"
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
                                agent._mail_queue.get(), timeout=0.5
                            )
                            sid = f"{agent.id}-{mail.id}"
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
                            with agent._session_span(session):
                                if session.state == SessionState.WAITING:
                                    await agent._collect_session_results(session)
                                elif session.state == SessionState.ACTIVE:
                                    mail = session_mails[sid]
                                    await agent._step_session(session, mail)
                        except Exception as e:
                            logger.error(
                                "session_error", session_id=sid, error=str(e), exc_info=True
                            )
                            session.mark_completed()

                    # ── 4. Cleanup: the mail is processed, cursor advances ──
                    if session.is_completed:
                        await session.cancel_pending()
                        await agent._mark_mail_read(session_mails.get(sid))
                        del sessions[sid]
                        session_mails.pop(sid, None)
                        self._last_activity_at = datetime.now()

                # ── 5. Agent state derives from live sessions ──
                agent._derive_state(sessions)

                # Yield control
                await asyncio.sleep(0.01 if sessions else 0.05)

            logger.info("agent_main_loop_stopped")

    def _is_session_timed_out(self, session: AgentSession) -> bool:
        """A session idle beyond the configured timeout is considered stuck."""
        timeout = self._agent.config.session_timeout_seconds
        idle = (datetime.now() - session.last_active_at).total_seconds()
        return idle > timeout
