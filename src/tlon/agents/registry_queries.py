"""Read-only registry queries bound to ordinary operations."""

from typing import TYPE_CHECKING, Any

from tlon.operations.registry import RegistryQueryPort

if TYPE_CHECKING:
    from tlon.agents.agent_registry import AgentRegistry


class RegistryQueries(RegistryQueryPort):
    """Adapt registry state to ordinary read operations for all members."""

    def __init__(self, registry: "AgentRegistry") -> None:
        self._registry = registry

    def is_sleeping(self, agent_id: Any) -> bool:
        """Whether the registry holds an explicit sleep intent."""
        if not isinstance(agent_id, str) or not agent_id:
            return False
        intent = self._registry._scheduling_intents.get(agent_id)
        return intent is not None and intent[0] == "sleep"

    def is_running(self, agent_id: Any) -> bool:
        """Currently in the run table."""
        return isinstance(agent_id, str) and self._registry.get_agent(agent_id) is not None

    def is_lazy(self, agent_id: Any) -> bool:
        """A member that can sleep / be drained (phase E: spec-derived)."""
        if not isinstance(agent_id, str):
            return False
        return self._registry.is_lazy(agent_id)

    def member_roster(self) -> str:
        """Render the public delivery directory, including offline members."""
        return "\n".join(
            f"{member['name']}（{member['id']}） — {member['address']}"
            for member in self._registry.list_members()
        )
