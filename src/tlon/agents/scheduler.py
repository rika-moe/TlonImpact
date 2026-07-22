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
from tlon.organization.messaging.message import Message

if TYPE_CHECKING:
    from tlon.agents.agent import Agent

logger = get_logger(__name__)


class SessionScheduler:
    """Multi-session scheduling loop for one agent."""

    def __init__(self, agent: "Agent"):
        self._agent = agent

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
            session_messages: dict[str, Message] = {}

            while agent._worker_running:
                # ── 0. Proactive memory triggers (no message required) ──
                await agent.memory.check_idle_timeout()
                await agent._check_reflection_trigger()

                # ── 1. New messages ──
                try:
                    msg, carrier = agent._message_queue.get_nowait()
                    sid = f"{agent.id}-{msg.id}"
                    session = AgentSession(sid, msg.content, carrier=carrier)
                    sessions[sid] = session
                    session_messages[sid] = msg
                    logger.info("session_started", session_id=sid, content=msg.content)
                except asyncio.QueueEmpty:
                    # No new messages — if no sessions either, block
                    if not sessions:
                        try:
                            msg, carrier = await asyncio.wait_for(
                                agent._message_queue.get(), timeout=0.5
                            )
                            sid = f"{agent.id}-{msg.id}"
                            sessions[sid] = AgentSession(sid, msg.content, carrier=carrier)
                            session_messages[sid] = msg
                        except TimeoutError:
                            continue

                # ── 2 & 3. Advance sessions ──
                for sid in list(sessions.keys()):
                    session = sessions[sid]
                    if session.id not in session_messages:
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
                                    msg = session_messages[sid]
                                    await agent._step_session(session, msg)
                        except Exception as e:
                            logger.error(
                                "session_error", session_id=sid, error=str(e), exc_info=True
                            )
                            session.mark_completed()

                    # ── 4. Cleanup ──
                    if session.is_completed:
                        await session.cancel_pending()
                        del sessions[sid]
                        session_messages.pop(sid, None)

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
