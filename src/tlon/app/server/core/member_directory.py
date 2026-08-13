"""Member Directory for Tlon Server.

Aggregates and provides information about all members in the system:
- Agents (from AgentRegistry, including sleeping ones)
- Humans (from AdapterManager — one online adapter per username)

This is the address book (通讯录用): member listing and lookup only.
Recipient→address resolution lives in AdapterManager.
"""

from tlon.agents.agent_registry import AgentRegistry
from tlon.base.logger import get_logger
from tlon.organization.messaging.human_adapter import AdapterManager
from tlon.organization.messaging.member_info import MemberInfo

logger = get_logger(__name__)


class MemberDirectory:
    """Discovers and aggregates all Congress members.

    Responsibilities:
    - Aggregate agents from AgentRegistry
    - Aggregate online humans from AdapterManager
    - Provide member lookup

    Note: This performs real-time aggregation on each call.
    If performance becomes an issue, consider adding caching.

    Example:
        >>> directory = MemberDirectory(agent_registry, adapter_manager)
        >>> all_members = directory.list_all()
        >>> agents = directory.list_agents()
        >>> humans = directory.list_humans()
    """

    def __init__(
        self,
        agent_registry: AgentRegistry,
        adapter_manager: AdapterManager,
    ):
        """Initialize the member directory.

        Args:
            agent_registry: Registry for agent information
            adapter_manager: Manager for online human adapters
        """
        self._agent_registry = agent_registry
        self._adapter_manager = adapter_manager

        logger.info("member_directory_initialized")

    def list_all(self) -> list[MemberInfo]:
        """Get all members (agents + online humans).

        This method performs real-time aggregation from both
        AgentRegistry and AdapterManager.

        Returns:
            List of all members
        """
        members: list[MemberInfo] = []
        members.extend(self._list_agents())
        members.extend(self.list_humans())
        return members

    def list_agents(self) -> list[MemberInfo]:
        """Get all agent members.

        Returns:
            List of agent members
        """
        return self._list_agents()

    def list_humans(self) -> list[MemberInfo]:
        """Get all online human members (one per username).

        Returns:
            List of human members, one per online adapter
        """
        return [
            MemberInfo(
                id=adapter.member_id,
                name=adapter.username,
                type="human",
                metadata={"session_count": adapter.session_count},
            )
            for adapter in self._adapter_manager.list_online()
        ]

    def get_member(self, member_id: str) -> MemberInfo | None:
        """Get a specific member by ID.

        Args:
            member_id: The member ID (agent ID or "human:<username>")

        Returns:
            The member if found, None otherwise
        """
        # Online human adapter?
        adapter = self._adapter_manager.get_adapter(
            member_id.removeprefix("human:")
        )
        if adapter is not None and adapter.member_id == member_id:
            return MemberInfo(
                id=adapter.member_id,
                name=adapter.username,
                type="human",
                metadata={"session_count": adapter.session_count},
            )

        # Check agents
        config = self._agent_registry.get_config(member_id)
        if config:
            return MemberInfo(
                id=member_id,
                name=config.name,
                type="agent",
                metadata={},
            )

        return None

    def get_member_names(self) -> list[str]:
        """Get names of all members.

        Returns:
            List of member display names
        """
        return [m.name for m in self.list_all()]

    def _list_agents(self) -> list[MemberInfo]:
        """Internal: List all agent members.

        Returns:
            List of agent members
        """
        members: list[MemberInfo] = []

        for agent_id in self._agent_registry.get_all():
            config = self._agent_registry.get_config(agent_id)
            if config:
                members.append(
                    MemberInfo(
                        id=agent_id,
                        name=config.name,
                        type="agent",
                        metadata={},
                    )
                )

        return members

    def count_agents(self) -> int:
        """Get the number of agents.

        Returns:
            Number of registered agents
        """
        return len(self._agent_registry.get_all())

    def count_humans(self) -> int:
        """Get the number of online human members.

        Returns:
            Number of online human adapters (one per username)
        """
        return len(self._adapter_manager.list_online())

    def count_all(self) -> int:
        """Get the total number of members.

        Returns:
            Total member count (agents + online humans)
        """
        return self.count_agents() + self.count_humans()
