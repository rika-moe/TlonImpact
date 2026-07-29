"""Lifecycle request payloads for the policy/mechanism split (阶段 D).

Policy (who should run) and mechanism (how an agent is assembled and run)
are separated: Adam (tlon.agents.adam) is the policy — it observes the bus
and issues *decisions*; the AgentRegistry is pure mechanism — it subscribes
to these request events and executes them (assembly, run table, locks).

The payload dataclasses below are the contract between the two. They are
deliberately small: the registry re-checks all run-state itself before
acting, so requests carry intent, not state.

See docs/design/members.md (阶段 D).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class StartRequest:
    """Adam's decision that an agent should be running.

    Payload of AGENT_START_REQUEST. The registry executes it idempotently:
    an already-running or already-waking agent is skipped.

    Attributes:
        agent_id: ID of the agent to start
        reason: Why the start was requested — "boot" (activation=="always"
            at server startup) or "wake" (mail delivered to a sleeping
            agent's inbox)
    """

    agent_id: str
    reason: str


@dataclass(frozen=True)
class StopRequest:
    """Adam's decision that an agent should be stopped.

    Payload of AGENT_STOP_REQUEST. The registry re-validates under the
    per-agent wake lock (the agent may have gained a session or fresh
    activity since the idle fact was reported) before executing.

    Attributes:
        agent_id: ID of the agent to stop
        reason: Why the stop was requested — "drain" (idle lazy agent)
    """

    agent_id: str
    reason: str
