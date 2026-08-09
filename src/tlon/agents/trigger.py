"""Member identity snapshot (unified runtime).

The former ``TriggerEvaluator`` (the react should-respond gate) was absorbed
into the unified :class:`~tlon.agents.member_runtime.MemberRuntime` — its
``:when`` predicate now evaluates under the unified env. This module keeps
only the immutable identity snapshot the cognitive subsystems take as plain
data.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentIdentity:
    """Immutable identity snapshot of a member, for the cognitive engine."""

    id: str
    name: str
    address: str
