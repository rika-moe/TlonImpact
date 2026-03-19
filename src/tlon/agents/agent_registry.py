"""Agent Registry for managing multiple agents.

This module provides AgentRegistry class that manages:
- Agent lifecycle (all agents start at system startup)
- Agent shutdown

Configuration is delegated to AgentInfoProvider (typically ConfigManager).

Architecture:
    AgentRegistry is a Component that depends on:
    - EventBus (for agent event publishing)
    - AgentInfoProvider (for configuration queries)
    - PositionRegistry (for position lookups)
    - MessageStore (for agent history access)

    All dependencies are injected via constructor (DAG rule).
"""

import asyncio
from typing import TYPE_CHECKING

from tlon.agents.agent import Agent
from tlon.base.component import Component
from tlon.base.config import AgentConfig
from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus
from tlon.base.logger import get_logger
from tlon.organization.messaging.store import MessageStore
from tlon.organization.positions import PositionRegistry

if TYPE_CHECKING:
    from tlon.base.config.agent_info import AgentInfoProvider

logger = get_logger(__name__)


class AgentRegistry(Component):
    """Registry for managing multiple agents.

    All agents are started at system initialization.
    No lazy loading support.

    Configuration is provided by AgentInfoProvider (typically ConfigManager),
    allowing this class to focus on agent lifecycle management only.

    Dependencies (constructor injected):
        - event_bus: EventBus for agent event publishing
        - agent_info_provider: AgentInfoProvider for configuration queries
        - position_registry: PositionRegistry (optional)
        - message_store: MessageStore (optional)

    Example:
        >>> registry = AgentRegistry(
        ...     event_bus=event_bus,
        ...     agent_info_provider=config_manager,
        ...     position_registry=position_registry,
        ...     message_store=message_store,
        ... )
        >>> registry.register("dev-001")  # Register by ID
        >>> await registry.initialize()  # Start all agents
        >>> agent = registry.get_agent("dev-001")  # Get running agent
    """

    def __init__(
        self,
        event_bus: EventBus,
        agent_info_provider: "AgentInfoProvider",
        position_registry: PositionRegistry | None = None,
        message_store: MessageStore | None = None,
    ):
        """Initialize the agent registry.

        All dependencies are injected via constructor (DAG architecture).

        Args:
            event_bus: Event bus for message routing
            agent_info_provider: Provider for agent configuration queries
            position_registry: Position registry for looking up position info
            message_store: Message store for agent history access
        """
        super().__init__(name="AgentRegistry")
        self.event_bus = event_bus
        self._agent_info = agent_info_provider
        self.position_registry = position_registry
        self.message_store = message_store

        # Registered agent IDs (to be started during initialize)
        self._registered_ids: list[str] = []

        # Active agents (all agents are active after initialize)
        self._agents: dict[str, Agent] = {}

        logger.info("AgentRegistry constructed")

    def register(self, agent_id: str) -> None:
        """Register an agent to be started.

        The agent configuration is looked up from AgentInfoProvider.
        The agent will be started during initialize().

        Args:
            agent_id: The agent ID to register

        Raises:
            ValueError: If agent configuration is not found
        """
        if not self._agent_info.get_config(agent_id):
            raise ValueError(f"Cannot register unknown agent: {agent_id}")

        if agent_id not in self._registered_ids:
            self._registered_ids.append(agent_id)
            logger.info(f"Registered agent: {agent_id}")

    def register_all(self, agent_ids: list[str]) -> None:
        """Register multiple agents.

        Args:
            agent_ids: List of agent IDs
        """
        for agent_id in agent_ids:
            self.register(agent_id)

    async def initialize(self) -> None:
        """Initialize the registry and start all registered agents."""
        logger.info("Initializing AgentRegistry...")
        logger.info(f"Agents to start: {self._registered_ids}")

        # Start all registered agents
        for agent_id in list(self._registered_ids):
            if agent_id not in self._agents:
                logger.info(f"Starting agent: {agent_id}")
                try:
                    await self._start_agent(agent_id)
                    logger.info(f"Agent {agent_id} started successfully")
                except Exception as e:
                    logger.error(f"Failed to start agent {agent_id}: {e}", exc_info=True)

        self._initialized = True
        active_count = len(self._agents)
        logger.info(f"AgentRegistry initialized: {active_count} active agents")
        logger.info(f"Active agents: {list(self._agents.keys())}")

    async def shutdown(self) -> None:
        """Shutdown all agents and cleanup."""
        logger.info("Shutting down AgentRegistry...")

        # Shutdown all active agents
        for agent_id, agent in list(self._agents.items()):
            try:
                await agent.shutdown()
                logger.info(f"Agent {agent_id} shutdown")
            except Exception as e:
                logger.error(f"Error shutting down agent {agent_id}: {e}")

        self._agents.clear()
        self._registered_ids.clear()
        self._initialized = False

        logger.info("AgentRegistry shutdown complete")

    async def _start_agent(self, agent_id: str) -> Agent | None:
        """Start an agent.

        Internal method called by initialize().

        Args:
            agent_id: ID of the agent to start

        Returns:
            The started Agent instance, or None if not found
        """
        if agent_id in self._agents:
            # Already active
            return self._agents[agent_id]

        config = self._agent_info.get_config(agent_id)
        if not config:
            logger.error(f"Cannot start unknown agent: {agent_id}")
            return None

        try:
            logger.info(f"Starting agent: {config.name} ({agent_id})")

            # Prepare team members config (all other agents)
            all_ids = self._agent_info.get_all_ids()
            team_members = [
                self._agent_info.get_config(aid)
                for aid in all_ids
                if aid != agent_id and self._agent_info.get_config(aid)
            ]

            # Create and initialize agent
            agent = Agent(
                config=config,
                event_bus=self.event_bus,
                position_registry=self.position_registry,
                team_members=team_members,
                message_store=self.message_store,  # Agents can now query history!
            )
            await agent.initialize()

            # Track active agent
            self._agents[agent_id] = agent

            # Publish agent started event
            event = Event(
                type=EventType.AGENT_STARTED,
                payload={"agent_id": agent_id, "name": config.name},
                source="agent_registry",
            )
            await self.event_bus.publish(event)

            logger.info(f"Agent {config.name} started successfully")
            return agent

        except Exception as e:
            logger.error(f"Failed to start agent {agent_id}: {e}", exc_info=True)
            return None

    def get_agent(self, agent_id: str) -> Agent | None:
        """Get an active agent by ID.

        Args:
            agent_id: Agent ID

        Returns:
            The Agent instance if active, None otherwise
        """
        return self._agents.get(agent_id)

    def get_all_agents(self) -> list[Agent]:
        """Get all currently active agents.

        Returns:
            List of active Agent instances
        """
        return list(self._agents.values())

    def get_all_ids(self) -> list[str]:
        """Get all registered agent IDs.

        Returns:
            List of all agent IDs from AgentInfoProvider
        """
        return list(self._agent_info.get_all_ids())

    def get_all(self) -> list[str]:
        """Get all registered agent IDs.

        Alias for get_all_ids() for backward compatibility.

        Returns:
            List of all agent IDs
        """
        return self.get_all_ids()

    def get_config(self, agent_id: str) -> AgentConfig | None:
        """Get an agent's configuration.

        Delegates to AgentInfoProvider.

        Args:
            agent_id: Agent ID

        Returns:
            AgentConfig or None if not found
        """
        return self._agent_info.get_config(agent_id)

    def unregister(self, agent_id: str) -> bool:
        """Unregister and stop an agent.

        Args:
            agent_id: Agent ID to unregister

        Returns:
            True if agent was found and removed
        """
        # Check if registered or active
        is_registered = agent_id in self._registered_ids
        is_active = agent_id in self._agents

        if not is_registered and not is_active:
            return False

        # Stop if active
        if is_active:
            agent = self._agents.pop(agent_id)
            asyncio.create_task(agent.shutdown())

        # Remove from registered list
        if is_registered:
            self._registered_ids.remove(agent_id)

        logger.info(f"Unregistered agent: {agent_id}")
        return True

    def update_config(self, agent_id: str, config: AgentConfig) -> bool:
        """Update an agent's configuration.

        Note: Configuration updates should be done through AgentInfoProvider
        (e.g., ConfigManager). This method only validates that the agent
        is registered and logs a warning if the agent is active.

        Args:
            agent_id: Agent ID to update
            config: New configuration (for validation only)

        Returns:
            True if agent is registered
        """
        if agent_id not in self._registered_ids and agent_id not in self._agents:
            return False

        # If agent is active, we should restart it
        # For now, just log the warning
        if agent_id in self._agents:
            logger.warning(
                f"Agent {agent_id} is active, config updated but restart required for changes"
            )

        logger.info(f"Config update notification for agent: {agent_id}")
        return True

    def get_agent_status(self, agent_id: str) -> dict | None:
        """Get status of a specific agent.

        Args:
            agent_id: Agent ID

        Returns:
            Agent status dictionary or None if not found
        """
        config = self._agent_info.get_config(agent_id)
        if not config:
            return None

        agent = self._agents.get(agent_id)

        return {
            "id": agent_id,
            "name": config.name,
            "state": agent.get_state() if agent else "unknown",
            "position": config.position_id,
            "skills": config.skills,
        }

    def get_all_agent_status(self) -> list[dict]:
        """Get status of all registered agents.

        Returns:
            List of agent status dictionaries
        """
        return [self.get_agent_status(agent_id) for agent_id in self._agent_info.get_all_ids()]
