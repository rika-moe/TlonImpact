"""Idle reclamation loop (split from agent_registry).

``IdleReaper`` runs the registry's idle-*fact* monitor: every
``IDLE_CHECK_INTERVAL_SECONDS`` it publishes an AGENT_IDLE fact for each
running lazy agent idle past its timeout with no live sessions —
deciding what to do with that fact is Adam's policy, not the registry's.
It also executes the resulting drains (``drain``): a stop request is
re-validated under the per-agent wake lock right before shutdown, so a
session that started in the race window keeps the agent alive.

The registry is constructor-injected for the run table, config queries,
wake locks, and the event bus; the import direction stays
one-directional (agent_registry → reaper, the back-reference is a type
annotation only).
"""

import asyncio
from typing import TYPE_CHECKING

from tlon.base.events.event import Event, EventType
from tlon.base.logger import get_logger

if TYPE_CHECKING:
    from tlon.agents.agent_registry import AgentRegistry

logger = get_logger(__name__)


class IdleReaper:
    """Idle fact detection + drain execution for a registry.

    Created and owned by ``AgentRegistry``; the registry runs ``run()``
    as its idle monitor task and delegates stop-request execution to
    ``drain``.
    """

    # Idle monitor check period (seconds)
    IDLE_CHECK_INTERVAL_SECONDS = 30

    def __init__(self, registry: "AgentRegistry") -> None:
        self._registry = registry

    async def run(self) -> None:
        """Periodically report idle lazy agents (scale-to-zero facts).

        Every ``IDLE_CHECK_INTERVAL_SECONDS``, each running lazy agent that
        has been idle past its configured timeout and has no live sessions
        is reported as an AGENT_IDLE fact event. Stopping it is a policy
        decision (Adam's); the registry only executes the resulting
        AGENT_STOP_REQUEST.
        """
        registry = self._registry
        while True:
            await asyncio.sleep(self.IDLE_CHECK_INTERVAL_SECONDS)
            for agent_id in list(registry._agents.keys()):
                try:
                    await self._report_if_idle(agent_id)
                except Exception as e:
                    logger.error("agent_idle_check_failed", agent_id=agent_id, error=str(e))

    async def _report_if_idle(self, agent_id: str) -> None:
        """Publish an AGENT_IDLE fact if the agent is idle past its timeout.

        Fact detection only — no state is changed here. An agent with any
        live (ACTIVE/WAITING) session is never reported idle.
        """
        registry = self._registry
        config = registry._agent_info.get_config(agent_id)
        if not config or not registry.is_lazy(agent_id):
            return

        agent = registry._agents.get(agent_id)
        if agent is None:
            return
        if agent.active_session_count > 0 or agent.idle_seconds < config.idle_timeout_seconds:
            return

        idle = int(agent.idle_seconds)
        logger.info("agent_idle_detected", agent_id=agent_id, idle_seconds=idle)
        await registry.event_bus.publish(
            Event(
                type=EventType.AGENT_IDLE,
                payload={"agent_id": agent_id, "idle_seconds": idle},
                source="agent_registry",
            )
        )

    async def drain(self, agent_id: str, reason: str) -> None:
        """Execute a stop request: drain one idle lazy agent.

        The fact that motivated the request may be stale, so the condition
        is re-checked under the per-agent lock right before shutdown — a
        session that started (or activity that resumed) in the race window
        keeps the agent alive.
        """
        registry = self._registry
        config = registry._agent_info.get_config(agent_id)
        if not config or not registry.is_lazy(agent_id):
            return

        agent = registry._agents.get(agent_id)
        if agent is None:
            return
        if agent.active_session_count > 0 or agent.idle_seconds < config.idle_timeout_seconds:
            return

        # Serialize with a concurrent start of the same persona
        lock = registry._wake_locks.setdefault(agent_id, asyncio.Lock())
        async with lock:
            agent = registry._agents.get(agent_id)
            if agent is None:
                return
            # Drain re-check under the lock
            if agent.active_session_count > 0:
                return
            if agent.idle_seconds < config.idle_timeout_seconds:
                return

            registry._agents.pop(agent_id)
            idle = int(agent.idle_seconds)
            try:
                await agent.shutdown()
            except asyncio.CancelledError:
                registry._agents[agent_id] = agent
                raise
            except Exception as e:
                # The agent is already out of the run table; a later
                # start request assembles a fresh activation.
                logger.error("agent_sleep_failed", agent_id=agent_id, error=str(e))
                return

            # Unified member model (ADR 0006): draining always means
            # sleep — members are never discarded, their home directories
            # are archived forever.
            logger.info("agent_sleeping", agent_id=agent_id, idle_seconds=idle, reason=reason)
            await registry.event_bus.publish(
                Event(
                    type=EventType.AGENT_SLEEPING,
                    payload={"agent_id": agent_id, "idle_seconds": idle},
                    source="agent_registry",
                )
            )
