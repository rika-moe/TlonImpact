"""Agent information provider interface.

This module defines the AgentInfoProvider protocol for decoupling
agent configuration access from specific implementations.
"""

from typing import Protocol

from tlon.base.config.models import AgentConfig


class AgentInfoProvider(Protocol):
    """Protocol for providing agent configuration information.

    This protocol abstracts the source of agent configuration,
    allowing components to query agent info without depending on
    the full ConfigManager or AgentRegistry.

    Implementations:
    - ConfigManager: File-based configuration
    - Future: DatabaseAgentStore for dynamic agent management

    Example:
        >>> provider: AgentInfoProvider = config_manager
        >>> config = provider.get_config("dev-001")
        >>> if provider.is_proactive("dev-001"):
        ...     print("Agent responds to all messages")
    """

    def get_config(self, agent_id: str) -> AgentConfig | None:
        """Get agent configuration by ID.

        Args:
            agent_id: The unique agent identifier

        Returns:
            AgentConfig if found, None otherwise
        """
        ...

    def get_all_ids(self) -> set[str]:
        """Get all agent IDs.

        Returns:
            Set of all registered agent IDs
        """
        ...

    def is_proactive(self, agent_id: str) -> bool:
        """Check if an agent is proactive (responds to all messages).

        Args:
            agent_id: The agent identifier

        Returns:
            True if agent responds to general messages without @mentions
        """
        ...
