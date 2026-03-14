"""Agent Registry for managing multiple agent lifecycle.

This module provides AgentRegistry class that manages:
- Agent configuration registration
- Standby pool for lazy loading
- Activation/deactivation of agents
- Resident vs lazy loading strategies
"""

from typing import Any, Dict, List, Optional, Set

from tlon.organization.messaging import Congress, Participant
from tlon.base.events.event import Event, EventType
from tlon.organization.messaging import EventBus
from tlon.organization.messaging.message import Message
from tlon.base.logger import get_logger
from tlon.agents.agent import Agent
from tlon.base.config import AgentConfig
from tlon.organization.positions import PositionRegistry

logger = get_logger(__name__)


class AgentRegistry:
    """Registry for managing multiple agents with lifecycle control.
    
    Supports two loading strategies:
    - Resident: Agent starts immediately with application
    - Lazy: Agent starts on first @mention
    
    Example:
        >>> registry = AgentRegistry(event_bus, chatroom, position_registry)
        >>> registry.register(agent_config)  # Add to standby pool
        >>> await registry.initialize()  # Start resident agents
        >>> agent = await registry.activate("dev-001")  # Activate lazy agent
    """
    
    def __init__(
        self,
        event_bus: EventBus,
        chatroom: Congress,
        position_registry: Optional[PositionRegistry] = None
    ):
        """Initialize the agent registry.
        
        Args:
            event_bus: Event bus for message routing
            chatroom: Chat room for participant management
            position_registry: Position registry for looking up position info
        """
        self.event_bus = event_bus
        self.chatroom = chatroom
        self.position_registry = position_registry
        
        # Configuration storage
        self._configs: Dict[str, AgentConfig] = {}
        
        # Active agents
        self._agents: Dict[str, Agent] = {}
        
        # Standby agent IDs (registered but not activated)
        self._standby: Set[str] = set()
        
        # Resident agent IDs (start immediately)
        self._resident_ids: Set[str] = set()
        
        # Event subscription for activation
        self._event_subscription: Optional[str] = None
        
        logger.info("AgentRegistry initialized")
    
    def register(self, config: AgentConfig) -> None:
        """Register an agent configuration.
        
        The agent is added to standby pool and will be activated
        based on its loading strategy (resident or lazy).
        
        Args:
            config: Agent configuration
        """
        self._configs[config.id] = config
        self._standby.add(config.id)
        
        # Determine if proactive from agent config
        if config.proactive:
            # Proactive agents are resident (start immediately)
            self._resident_ids.add(config.id)
            logger.info(f"Registered resident agent: {config.name} ({config.id}) - proactive=True")
        else:
            logger.info(f"Registered lazy-load agent: {config.name} ({config.id}) - proactive=False")
    
    def register_all(self, configs: List[AgentConfig]) -> None:
        """Register multiple agent configurations.
        
        Args:
            configs: List of agent configurations
        """
        for config in configs:
            self.register(config)
    
    async def initialize(self) -> None:
        """Initialize the registry and start resident agents.
        
        Resident agents (proactive=True) are started immediately.
        Lazy agents remain in standby until activated.
        """
        logger.info("Initializing AgentRegistry...")
        logger.info(f"Resident agents to start: {self._resident_ids}")
        logger.info(f"Standby agents: {self._standby}")
        
        # Subscribe to message events for lazy activation
        self._event_subscription = self.event_bus.subscribe(
            EventType.MESSAGE_NEW,
            self._on_message_for_activation
        )
        
        # Start resident agents
        for agent_id in list(self._resident_ids):
            if agent_id in self._standby:
                logger.info(f"Starting resident agent: {agent_id}")
                try:
                    await self.activate(agent_id)
                    logger.info(f"Resident agent {agent_id} started successfully")
                except Exception as e:
                    logger.error(f"Failed to start resident agent {agent_id}: {e}", exc_info=True)
            else:
                logger.warning(f"Resident agent {agent_id} not in standby, cannot start")
        
        standby_count = len(self._standby)
        active_count = len(self._agents)
        logger.info(f"AgentRegistry initialized: {active_count} active, {standby_count} standby")
        logger.info(f"Active agents: {list(self._agents.keys())}")
    
    async def shutdown(self) -> None:
        """Shutdown all agents and cleanup."""
        logger.info("Shutting down AgentRegistry...")
        
        # Unsubscribe from events
        if self._event_subscription:
            self.event_bus.unsubscribe(self._event_subscription)
        
        # Shutdown all active agents
        for agent_id, agent in list(self._agents.items()):
            try:
                await agent.shutdown()
                logger.info(f"Agent {agent_id} shutdown")
            except Exception as e:
                logger.error(f"Error shutting down agent {agent_id}: {e}")
        
        self._agents.clear()
        self._standby.clear()
        self._resident_ids.clear()
        self._configs.clear()
        
        logger.info("AgentRegistry shutdown complete")
    
    async def activate(self, agent_id: str) -> Optional[Agent]:
        """Activate (start) an agent from standby.
        
        Args:
            agent_id: ID of the agent to activate
            
        Returns:
            The activated Agent instance, or None if not found
        """
        if agent_id in self._agents:
            # Already active
            return self._agents[agent_id]
        
        if agent_id not in self._configs:
            logger.error(f"Cannot activate unknown agent: {agent_id}")
            return None
        
        config = self._configs[agent_id]
        
        try:
            logger.info(f"Activating agent: {config.name} ({agent_id})")
            
            # Prepare team members config (all other agents)
            team_members = [
                cfg for cfg in self._configs.values()
                if cfg.id != agent_id
            ]
            
            # Create and initialize agent
            agent = Agent(
                config=config,
                event_bus=self.event_bus,
                chatroom=self.chatroom,
                position_registry=self.position_registry,
                team_members=team_members,
            )
            await agent.initialize()
            
            # Track active agent
            self._agents[agent_id] = agent
            self._standby.discard(agent_id)
            
            # Add to chatroom participants
            participant = Participant(
                id=config.id,
                name=config.name,
                type="agent",
                metadata={"position_id": config.position_id},
            )
            await self.chatroom.join(participant)
            
            # Publish agent started event
            from ..protocol import Event
            event = Event(
                type=EventType.AGENT_STARTED,
                payload={"agent_id": agent_id, "name": config.name},
                source="agent_registry",
            )
            await self.event_bus.publish(event)
            
            logger.info(f"Agent {config.name} activated successfully")
            return agent
            
        except Exception as e:
            logger.error(f"Failed to activate agent {agent_id}: {e}", exc_info=True)
            return None
    
    async def deactivate(self, agent_id: str) -> None:
        """Deactivate (stop) an active agent.
        
        The agent is stopped but its configuration remains registered.
        It can be re-activated later.
        
        Args:
            agent_id: ID of the agent to deactivate
        """
        if agent_id not in self._agents:
            return
        
        agent = self._agents.pop(agent_id)
        
        try:
            await agent.shutdown()
            self._standby.add(agent_id)
            
            # Publish agent stopped event
            from ..protocol import Event
            event = Event(
                type=EventType.AGENT_STOPPED,
                payload={"agent_id": agent_id},
                source="agent_registry",
            )
            await self.event_bus.publish(event)
            
            logger.info(f"Agent {agent_id} deactivated")
        except Exception as e:
            logger.error(f"Error deactivating agent {agent_id}: {e}")
    
    def get_agent(self, agent_id: str) -> Optional[Agent]:
        """Get an active agent by ID.
        
        Args:
            agent_id: Agent ID
            
        Returns:
            The Agent instance if active, None otherwise
        """
        return self._agents.get(agent_id)
    
    def get_all_active_agents(self) -> List[Agent]:
        """Get all currently active agents.
        
        Returns:
            List of active Agent instances
        """
        return list(self._agents.values())
    
    def get_all_ids(self) -> List[str]:
        """Get all registered agent IDs (both active and standby).
        
        Returns:
            List of all agent IDs
        """
        # Combine configs, active agents, and standby
        all_ids = set(self._configs.keys())
        all_ids.update(self._agents.keys())
        all_ids.update(self._standby)
        return list(all_ids)
    
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
        """Unregister and deactivate an agent.
        
        Args:
            agent_id: Agent ID to unregister
            
        Returns:
            True if agent was found and removed
        """
        if agent_id not in self._configs and agent_id not in self._agents and agent_id not in self._standby:
            return False
        
        # Deactivate if active
        if agent_id in self._agents:
            agent = self._agents[agent_id]
            asyncio.create_task(agent.stop())
            del self._agents[agent_id]
        
        # Remove from all tracking sets
        self._configs.pop(agent_id, None)
        self._standby.discard(agent_id)
        self._resident_ids.discard(agent_id)
        
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
    
    async def update_position_references(self, new_positions: Dict[str, Any]) -> None:
        """Update position references for all agents.
        
        This is called when positions are reloaded.
        
        Args:
            new_positions: New position configurations
        """
        # Update position registry reference
        if self.position_registry:
            # Position registry is updated externally
            pass
        
        # Notify active agents of position changes if needed
        for agent_id, agent in self._agents.items():
            # Agents may need to refresh their position info
            pass
        
        logger.info(f"Updated position references for {len(self._configs)} agents")
    
    def get_standby_ids(self) -> List[str]:
        """Get IDs of agents in standby mode.
        
        Returns:
            List of standby agent IDs
        """
        return list(self._standby)
    
    def is_active(self, agent_id: str) -> bool:
        """Check if an agent is currently active.
        
        Args:
            agent_id: Agent ID
            
        Returns:
            True if the agent is active
        """
        return agent_id in self._agents
    
    def is_standby(self, agent_id: str) -> bool:
        """Check if an agent is in standby mode.
        
        Args:
            agent_id: Agent ID
            
        Returns:
            True if the agent is in standby
        """
        return agent_id in self._standby
    
    def get_all_agent_status(self) -> List[Dict]:
        """Get status of all registered agents.
        
        Returns:
            List of agent status dictionaries
        """
        statuses = []
        
        # Get all registered agent IDs
        all_ids = set(self._configs.keys())
        
        for agent_id in all_ids:
            config = self._configs.get(agent_id)
            agent = self._agents.get(agent_id)
            
            from .agent import AgentState
            
            if agent:
                state = agent.state.value
            elif agent_id in self._standby:
                state = AgentState.STANDBY.value
            else:
                state = "unknown"
            
            status = {
                "id": agent_id,
                "name": config.name if config else agent_id,
                "state": state,
                "position": config.position_id if config else "unknown",
                "skills": config.skills if config else [],
            }
            statuses.append(status)
        
        return statuses
    
    async def wake_agent(self, agent_id: str) -> bool:
        """Wake up a lazy agent.
        
        Alias for activate() for semantic clarity.
        
        Args:
            agent_id: Agent ID to wake
            
        Returns:
            True if agent was woken successfully
        """
        if agent_id in self._agents:
            # Already active
            return True
        
        if agent_id not in self._standby:
            # Not in standby (either not registered or already active)
            return False
        
        agent = await self.activate(agent_id)
        return agent is not None
    
    async def stop_agent(self, agent_id: str) -> bool:
        """Stop an active agent.
        
        Alias for deactivate() for semantic clarity.
        
        Args:
            agent_id: Agent ID to stop
            
        Returns:
            True if agent was stopped successfully
        """
        if agent_id not in self._agents:
            # Not active
            return False
        
        await self.deactivate(agent_id)
        return True
    
    async def _on_message_for_activation(self, event: Event) -> None:
        """Handle messages to check for lazy agent activation.
        
        If a message @mentions a standby agent, activate it and
        re-deliver the message to the newly activated agent.
        
        Args:
            event: The message event
        """
        message: Message = event.payload
        
        # Check if message mentions any standby agents
        for agent_id in list(self._standby):
            config = self._configs.get(agent_id)
            if not config:
                continue
            
            # Check if mentioned by ID or name
            is_mentioned = (
                agent_id in message.mentions or
                config.name in message.mentions
            )
            
            if is_mentioned:
                # Notify user that agent is waking up
                await self._notify_waking_up(config.name)
                
                # Activate the agent
                agent = await self.activate(agent_id)
                
                # Re-deliver the triggering message to the newly activated agent
                # This is crucial because the agent missed the message while starting up
                if agent:
                    logger.debug(f"Re-delivering message to newly activated agent {agent_id}")
                    await agent._on_message(event)
    
    async def _notify_waking_up(self, agent_name: str) -> None:
        """Send a system message indicating an agent is waking up.
        
        Args:
            agent_name: Name of the agent being activated
        """
        try:
            system_msg = Message.create(
                sender_id="system",
                sender_name="系统",
                sender_type="system",
                content=f"正在唤醒 {agent_name}...",
                content_type="system",
            )
            await self.chatroom.send_message(system_msg)
        except Exception as e:
            logger.error(f"Failed to send wake-up notification: {e}")
