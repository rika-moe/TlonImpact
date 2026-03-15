"""Agent Registry for managing multiple agents.

This module provides AgentRegistry class that manages:
- Agent configuration registration
- Agent lifecycle (all agents start at system startup)
- Agent shutdown
"""
import asyncio
from typing import Dict, List, Optional

from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus
from tlon.base.logger import get_logger
from tlon.agents.agent import Agent
from tlon.base.config import AgentConfig
from tlon.organization.positions import PositionRegistry
from tlon.organization.messaging.store import MessageStore

logger = get_logger(__name__)


class AgentRegistry:
    """Registry for managing multiple agents.
    
    All agents are started at system initialization.
    No lazy loading support.
    
    Example:
        >>> registry = AgentRegistry(event_bus, position_registry)
        >>> registry.register(agent_config)  # Register config
        >>> await registry.initialize()  # Start all agents
        >>> agent = registry.get_agent("dev-001")  # Get running agent
    """
    
    def __init__(
        self,
        event_bus: EventBus,
        position_registry: Optional[PositionRegistry] = None,
        message_store: Optional[MessageStore] = None,
    ):
        """Initialize the agent registry.
        
        Args:
            event_bus: Event bus for message routing
            position_registry: Position registry for looking up position info
            message_store: Message store for agent history access
        """
        self.event_bus = event_bus
        self.position_registry = position_registry
        self.message_store = message_store
        
        # Configuration storage
        self._configs: Dict[str, AgentConfig] = {}
        
        # Active agents (all agents are active after initialize)
        self._agents: Dict[str, Agent] = {}
        
        logger.info("AgentRegistry initialized")
    
    def register(self, config: AgentConfig) -> None:
        """Register an agent configuration.
        
        The agent will be started during initialize().
        
        Args:
            config: Agent configuration
        """
        self._configs[config.id] = config
        logger.info(f"Registered agent: {config.name} ({config.id})")
    
    def register_all(self, configs: List[AgentConfig]) -> None:
        """Register multiple agent configurations.
        
        Args:
            configs: List of agent configurations
        """
        for config in configs:
            self.register(config)
    
    async def initialize(self) -> None:
        """Initialize the registry and start all registered agents."""
        logger.info("Initializing AgentRegistry...")
        logger.info(f"Agents to start: {list(self._configs.keys())}")

        # Start all registered agents
        for agent_id in list(self._configs.keys()):
            if agent_id not in self._agents:
                logger.info(f"Starting agent: {agent_id}")
                try:
                    await self._start_agent(agent_id)
                    logger.info(f"Agent {agent_id} started successfully")
                except Exception as e:
                    logger.error(f"Failed to start agent {agent_id}: {e}", exc_info=True)
        
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
        self._configs.clear()
        
        logger.info("AgentRegistry shutdown complete")
    
    async def _start_agent(self, agent_id: str) -> Optional[Agent]:
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
        
        if agent_id not in self._configs:
            logger.error(f"Cannot start unknown agent: {agent_id}")
            return None
        
        config = self._configs[agent_id]
        
        try:
            logger.info(f"Starting agent: {config.name} ({agent_id})")
            
            # Prepare team members config (all other agents)
            team_members = [
                cfg for cfg in self._configs.values()
                if cfg.id != agent_id
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
    
    def get_agent(self, agent_id: str) -> Optional[Agent]:
        """Get an active agent by ID.
        
        Args:
            agent_id: Agent ID
            
        Returns:
            The Agent instance if active, None otherwise
        """
        return self._agents.get(agent_id)
    
    def get_all_agents(self) -> List[Agent]:
        """Get all currently active agents.
        
        Returns:
            List of active Agent instances
        """
        return list(self._agents.values())
    
    def get_all_ids(self) -> List[str]:
        """Get all registered agent IDs.
        
        Returns:
            List of all agent IDs
        """
        return list(self._configs.keys())
    
    def get_all(self) -> List[str]:
        """Get all registered agent IDs.
        
        Alias for get_all_ids() for backward compatibility.
        
        Returns:
            List of all agent IDs
        """
        return self.get_all_ids()
    
    def get_config(self, agent_id: str) -> Optional[AgentConfig]:
        """Get an agent's configuration.
        
        Args:
            agent_id: Agent ID
            
        Returns:
            AgentConfig or None if not found
        """
        return self._configs.get(agent_id)
    
    def unregister(self, agent_id: str) -> bool:
        """Unregister and stop an agent.
        
        Args:
            agent_id: Agent ID to unregister
            
        Returns:
            True if agent was found and removed
        """
        if agent_id not in self._configs and agent_id not in self._agents:
            return False
        
        # Stop if active
        if agent_id in self._agents:
            agent = self._agents.pop(agent_id)
            asyncio.create_task(agent.shutdown())
        
        # Remove from configs
        self._configs.pop(agent_id, None)
        
        logger.info(f"Unregistered agent: {agent_id}")
        return True
    
    def update_config(self, agent_id: str, config: AgentConfig) -> bool:
        """Update an agent's configuration.
        
        This updates the stored config but does not restart the agent.
        The agent needs to be restarted for changes to take effect.
        
        Args:
            agent_id: Agent ID to update
            config: New configuration
            
        Returns:
            True if agent was found and updated
        """
        if agent_id not in self._configs:
            return False
        
        # Update config
        self._configs[agent_id] = config
        
        # If agent is active, we should restart it
        # For now, just log the warning
        if agent_id in self._agents:
            logger.warning(f"Agent {agent_id} is active, config updated but restart required for changes")
        
        logger.info(f"Updated config for agent: {agent_id}")
        return True
    
    def get_agent_status(self, agent_id: str) -> Optional[Dict]:
        """Get status of a specific agent.
        
        Args:
            agent_id: Agent ID
            
        Returns:
            Agent status dictionary or None if not found
        """
        config = self._configs.get(agent_id)
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
    
    def get_all_agent_status(self) -> List[Dict]:
        """Get status of all registered agents.
        
        Returns:
            List of agent status dictionaries
        """
        return [
            self.get_agent_status(agent_id)
            for agent_id in self._configs.keys()
        ]
