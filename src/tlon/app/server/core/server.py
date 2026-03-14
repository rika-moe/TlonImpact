"""Tlon Server Core.

The Server is a long-running process that manages the core functionality:
- Agent lifecycle management
- ChatRoom state and message history
- Event distribution
- Client session management

It exposes REST API for configuration and WebSocket for real-time updates.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Callable, Any

from tlon.organization.messaging import EventBus
from tlon.organization.messaging import Congress, CongressMetadata, Participant
from tlon.base.events.event import Event, EventType
from tlon.organization.messaging.message import Message
from tlon.agents.agent_registry import AgentRegistry
from tlon.organization.positions import PositionRegistry
from tlon.base.config import AgentConfig
from tlon.base.settings import settings
from tlon.base.logger import get_logger

logger = get_logger(__name__)


@dataclass
class ClientSession:
    """A client session connected to the server.
    
    Attributes:
        id: Unique session ID
        client_type: Client type ("tui", "web", "telegram")
        connected_at: Connection timestamp
        mode: Current operation mode ("chat", "event", "agent")
        filters: Event filters for this session
        metadata: Additional metadata
    """
    id: str
    client_type: str  # "tui" | "web" | "telegram"
    connected_at: str
    mode: str = "chat"  # "chat" | "event" | "agent"
    filters: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


class TlonServer:
    """Main server class for Tlon - pure coordinator with DI.
    
    TlonServer acts as a coordinator that receives all dependencies via
    dependency injection. It does NOT create any components, only coordinates
    their interaction.
    
    Responsibilities:
    - Client session management
    - Event distribution to connected clients
    - Coordinating configuration reloads
    
    All core components (EventBus, Congress, AgentRegistry) are created
    by ServerApp and injected into TlonServer.
    
    Example:
        >>> server = TlonServer(
        ...     event_bus=event_bus,
        ...     congress=congress,
        ...     agent_registry=agent_registry,
        ...     ...
        ... )
        >>> await server.initialize()
        >>> await server.start()
        >>> await server.shutdown()
    """
    
    def __init__(
        self,
        *,
        # Core components - all injected (DI)
        event_bus: EventBus,
        congress: Congress,
        position_registry: PositionRegistry,
        agent_registry: AgentRegistry,
        # Configuration management
        config_manager: Any,  # ConfigManager
        # Other parameters
        data_dir: Path,
        host: str,
        port: int,
    ):
        """Initialize the server with all dependencies injected.
        
        Args:
            event_bus: The EventBus instance (injected)
            congress: The Congress/ChatRoom instance (injected)
            position_registry: Position registry for lookups (injected)
            agent_registry: Agent registry for agent management (injected)
            config_manager: ConfigManager for reload support (injected)
            data_dir: Directory for persistence
            host: Host to bind the server to
            port: Port to listen on
        """
        # Store injected dependencies
        self._event_bus = event_bus
        self._congress = congress
        self._position_registry = position_registry
        self._agent_registry = agent_registry
        self._config_manager = config_manager
        
        self.host = host
        self.port = port
        self.data_dir = data_dir
        
        # Session management
        self.sessions: Dict[str, ClientSession] = {}
        self.session_counter = 0
        self._session_lock = asyncio.Lock()
        
        # Event subscriptions for clients
        self._client_handlers: Dict[str, Callable[[Event], None]] = {}
        
        # Server state
        self._running = False
        self._shutdown_event = asyncio.Event()
        
        logger.info(f"TlonServer initialized as coordinator (host={host}, port={port})")
    
    async def initialize(self) -> None:
        """Initialize TlonServer as coordinator.
        
        Validates all dependencies are properly injected and subscribes
        to events for broadcasting to clients.
        """
        logger.info("Initializing TlonServer as coordinator...")
        
        # Validate dependencies (all must be non-null)
        assert self._event_bus is not None, "EventBus not injected"
        assert self._congress is not None, "Congress not injected"
        assert self._agent_registry is not None, "AgentRegistry not injected"
        assert self._position_registry is not None, "PositionRegistry not injected"
        assert self._config_manager is not None, "ConfigManager not injected"
        
        logger.info("All dependencies validated")
        
        # Subscribe to events for broadcasting to clients
        self._subscribe_to_events()
        
        logger.info("TlonServer initialized as coordinator")
    
    # ========== Configuration Reload Handlers ==========
    
    async def on_positions_changed(self, new_positions: Dict[str, Any]) -> None:
        """Handle position configuration changes.
        
        Broadcasts CONFIG_RELOADED event to all connected clients.
        
        Args:
            new_positions: New position configurations
        """
        logger.info(f"Notifying clients of position reload: {len(new_positions)} positions")
        
        await self._event_bus.publish(Event(
            type=EventType.CONFIG_RELOADED,
            source="server",
            data={
                "type": "positions",
                "count": len(new_positions),
            }
        ))
    
    async def on_agents_changed(self, new_agents: Dict[str, Any]) -> None:
        """Handle agent configuration changes.
        
        Broadcasts CONFIG_RELOADED event to all connected clients.
        
        Args:
            new_agents: New agent configurations
        """
        logger.info(f"Notifying clients of agent reload: {len(new_agents)} agents")
        
        await self._event_bus.publish(Event(
            type=EventType.CONFIG_RELOADED,
            source="server",
            data={
                "type": "agents",
                "count": len(new_agents),
            }
        ))
    
    # ========== Property Accessors (for backward compatibility) ==========
    
    @property
    def event_bus(self) -> EventBus:
        """Access the injected EventBus."""
        return self._event_bus
    
    @property
    def chatroom(self) -> Congress:
        """Access the injected Congress (chatroom)."""
        return self._congress
    
    @property
    def agent_registry(self) -> AgentRegistry:
        """Access the injected AgentRegistry."""
        return self._agent_registry
    
    @property
    def position_registry(self) -> PositionRegistry:
        """Access the injected PositionRegistry."""
        return self._position_registry
    
    def _subscribe_to_events(self) -> None:
        """Subscribe to events for broadcasting to clients."""
        # Subscribe to message events
        self._event_bus.subscribe(
            EventType.MESSAGE_NEW,
            self._on_message_event
        )
        self._event_bus.subscribe(
            EventType.MESSAGE_MENTION,
            self._on_message_event
        )
        
        # Subscribe to agent events
        self._event_bus.subscribe(
            EventType.AGENT_STARTED,
            self._on_agent_event
        )
        self._event_bus.subscribe(
            EventType.AGENT_STOPPED,
            self._on_agent_event
        )
        self._event_bus.subscribe(
            EventType.AGENT_ERROR,
            self._on_agent_event
        )
    
    async def _on_message_event(self, event: Event) -> None:
        """Handle message events for broadcasting to clients."""
        logger.debug(f"[Server] Received message event: {event.type.value}")
        await self._broadcast_event(event)
    
    async def _on_agent_event(self, event: Event) -> None:
        """Handle agent events for broadcasting to clients."""
        logger.debug(f"[Server] Received agent event: {event.type.value}")
        await self._broadcast_event(event)
    
    async def _broadcast_event(self, event: Event) -> None:
        """Broadcast an event to all connected clients.
        
        Args:
            event: The event to broadcast
        """
        async with self._session_lock:
            handlers = list(self._client_handlers.items())
        
        if not handlers:
            logger.warning(f"[Server] No connected clients to receive event {event.type.value}")
            return
        
        logger.debug(f"[Server] Broadcasting event {event.type.value} to {len(handlers)} clients")
        
        for session_id, handler in handlers:
            try:
                # Get session to check filters
                session = self.sessions.get(session_id)
                if session and self._should_send_to_session(event, session):
                    logger.debug(f"[Server] Sending event {event.type.value} to session {session_id} (mode={session.mode})")
                    if asyncio.iscoroutinefunction(handler):
                        async def run_handler(h, e, sid):
                            try:
                                await h(e)
                            except Exception as ex:
                                logger.error(f"[Server] Handler error for session {sid}: {ex}")
                        asyncio.create_task(run_handler(handler, event, session_id))
                    else:
                        handler(event)
                else:
                    logger.debug(f"[Server] Filtering out event {event.type.value} for session {session_id}")
            except Exception as e:
                logger.error(f"[Server] Error broadcasting to session {session_id}: {e}", exc_info=True)
    
    def _should_send_to_session(self, event: Event, session: ClientSession) -> bool:
        """Check if an event should be sent to a session based on filters.
        
        Args:
            event: The event to check
            session: The client session
            
        Returns:
            True if the event should be sent
        """
        # Mode-based filtering
        if session.mode == "chat":
            # Chat mode: only send message events
            if event.type not in (EventType.MESSAGE_NEW, EventType.MESSAGE_MENTION):
                logger.debug(f"[Server] Event {event.type.value} filtered for session {session.id}: not a message event in chat mode")
                return False
        elif session.mode == "event":
            # Event mode: send all events, apply filters if set
            pass
        elif session.mode == "agent":
            # Agent mode: only send agent-related events
            if event.type not in (
                EventType.AGENT_STARTED,
                EventType.AGENT_STOPPED,
                EventType.AGENT_ERROR,
                EventType.AGENT_STATE_CHANGED,
            ):
                logger.debug(f"[Server] Event {event.type.value} filtered for session {session.id}: not an agent event in agent mode")
                return False
        
        # Apply custom filters
        filters = session.filters
        
        # Filter by agent
        if "agent" in filters:
            agent_filter = filters["agent"]
            source = getattr(event, 'source', None)
            if source and not self._match_wildcard(source, agent_filter):
                logger.debug(f"[Server] Event {event.type.value} filtered for session {session.id}: agent filter mismatch")
                return False
        
        # Filter by event type
        if "event_type" in filters:
            if event.type.value != filters["event_type"]:
                logger.debug(f"[Server] Event {event.type.value} filtered for session {session.id}: event_type filter mismatch")
                return False
        
        return True
    
    def _match_wildcard(self, value: str, pattern: str) -> bool:
        """Match a value against a wildcard pattern.
        
        Supports simple wildcards:
        - dev-* matches dev-001, dev-002, etc.
        - * matches everything
        
        Args:
            value: The value to match
            pattern: The pattern with wildcards
            
        Returns:
            True if value matches pattern
        """
        if pattern == "*":
            return True
        if "*" in pattern:
            prefix = pattern.replace("*", "")
            return value.startswith(prefix)
        return value == pattern
    
    async def start(self) -> None:
        """Start the server.
        
        This method blocks until the server is shut down.
        """
        self._running = True
        logger.info(f"TlonServer started on {self.host}:{self.port}")
        
        # Wait for shutdown signal
        await self._shutdown_event.wait()
        
        logger.info("TlonServer stopping...")
    
    async def shutdown(self) -> None:
        """Shutdown the server gracefully.
        
        Note: Core components are managed by ServerApp, TlonServer only
        handles client disconnections and state cleanup.
        """
        if not self._running:
            return
        
        logger.info("Shutting down TlonServer (coordinator)...")
        self._running = False
        self._shutdown_event.set()
        
        # Disconnect all clients
        async with self._session_lock:
            session_ids = list(self.sessions.keys())
        
        for session_id in session_ids:
            await self.disconnect_client(session_id)
        
        # Note: Component shutdown is handled by ServerApp (who created them)
        # We only clear our references
        logger.info("TlonServer coordinator shutdown complete")
    
    # ==================== Session Management ====================
    
    async def connect_client(
        self,
        client_type: str,
        event_handler: Callable[[Event], None],
        metadata: Optional[Dict[str, Any]] = None
    ) -> ClientSession:
        """Connect a new client session.
        
        Args:
            client_type: Type of client ("tui", "web", "telegram")
            event_handler: Callback for events
            metadata: Optional client metadata
            
        Returns:
            The created client session
        """
        async with self._session_lock:
            self.session_counter += 1
            session_id = f"{client_type}-{self.session_counter:04d}"
            
            session = ClientSession(
                id=session_id,
                client_type=client_type,
                connected_at=datetime.now().isoformat(),
                metadata=metadata or {},
            )
            
            self.sessions[session_id] = session
            self._client_handlers[session_id] = event_handler
        
        # Add human participant if first connection
        if len(self.sessions) == 1:
            human = Participant(
                id="human",
                name="人类",
                type="human",
            )
            await self.chatroom.join(human)
        
        logger.info(f"Client connected: {session_id} ({client_type})")
        return session
    
    async def disconnect_client(self, session_id: str) -> bool:
        """Disconnect a client session.
        
        Args:
            session_id: The session ID to disconnect
            
        Returns:
            True if session was found and removed
        """
        async with self._session_lock:
            session = self.sessions.pop(session_id, None)
            self._client_handlers.pop(session_id, None)
        
        if session:
            logger.info(f"Client disconnected: {session_id}")
            
            # Remove human participant if last connection
            async with self._session_lock:
                if len(self.sessions) == 0:
                    await self.chatroom.leave("human")
            
            return True
        
        return False
    
    def get_session(self, session_id: str) -> Optional[ClientSession]:
        """Get a session by ID.
        
        Args:
            session_id: The session ID
            
        Returns:
            The session or None if not found
        """
        return self.sessions.get(session_id)
    
    def get_all_sessions(self) -> List[ClientSession]:
        """Get all active sessions.
        
        Returns:
            List of all sessions
        """
        return list(self.sessions.values())
    
    async def update_session_mode(self, session_id: str, mode: str) -> bool:
        """Update a session's operation mode.
        
        Args:
            session_id: The session ID
            mode: New mode ("chat", "event", "agent")
            
        Returns:
            True if session was found and updated
        """
        session = self.sessions.get(session_id)
        if session:
            session.mode = mode
            logger.debug(f"Session {session_id} mode changed to {mode}")
            return True
        return False
    
    async def update_session_filters(
        self,
        session_id: str,
        filters: Dict[str, Any]
    ) -> bool:
        """Update a session's event filters.
        
        Args:
            session_id: The session ID
            filters: New filters
            
        Returns:
            True if session was found and updated
        """
        session = self.sessions.get(session_id)
        if session:
            session.filters = filters
            logger.debug(f"Session {session_id} filters updated: {filters}")
            return True
        return False
    
    # ==================== Chat Operations ====================
    
    async def send_message(
        self,
        content: str,
        sender_id: str = "human",
        sender_name: str = "人类",
        sender_type: str = "human",
        content_type: str = "text",
        mentions: Optional[List[str]] = None,
    ) -> Message:
        """Send a message to the chatroom.
        
        Args:
            content: Message content
            sender_id: Sender ID
            sender_name: Sender display name
            sender_type: Sender type ("human", "agent", "system")
            content_type: Content type ("text", "system", "code")
            mentions: List of mentioned agent IDs
            
        Returns:
            The created message
        """
        message = Message.create(
            sender_id=sender_id,
            sender_name=sender_name,
            sender_type=sender_type,
            content=content,
            content_type=content_type,
            mentions=mentions,
        )
        
        await self.chatroom.send_message(message)
        return message
    
    async def get_history(
        self,
        limit: int = 100,
        before_id: Optional[str] = None
    ) -> List[Message]:
        """Get chat history.
        
        Args:
            limit: Maximum number of messages
            before_id: Return messages before this ID
            
        Returns:
            List of messages
        """
        return await self.chatroom.get_history(limit, before_id)
    
    # ==================== Agent Operations ====================
    
    def register_agents(self, configs: List[AgentConfig]) -> None:
        """Register agent configurations.
        
        Args:
            configs: List of agent configurations
        """
        self.agent_registry.register_all(configs)
        logger.info(f"Registered {len(configs)} agents")
    
    async def initialize_agents(self) -> None:
        """Initialize registered agents (start resident agents)."""
        await self.agent_registry.initialize()
    
    async def wake_agent(self, agent_id: str) -> bool:
        """Wake up a lazy agent.
        
        Args:
            agent_id: The agent ID to wake
            
        Returns:
            True if agent was woken successfully
        """
        return await self.agent_registry.wake_agent(agent_id)
    
    async def stop_agent(self, agent_id: str) -> bool:
        """Stop an active agent.
        
        Args:
            agent_id: The agent ID to stop
            
        Returns:
            True if agent was stopped successfully
        """
        return await self.agent_registry.stop_agent(agent_id)
    
    def get_agent_status(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """Get an agent's status.
        
        Args:
            agent_id: The agent ID
            
        Returns:
            Agent status dict or None if not found
        """
        agent = self.agent_registry.get_agent(agent_id)
        if agent:
            return {
                "id": agent_id,
                "name": agent.config.name,
                "state": agent.state.value,
                "config": {
                    "role": agent.config.role.name,
                    "skills": agent.config.skills,
                }
            }
        return None
    
    def get_all_agent_statuses(self) -> List[Dict[str, Any]]:
        """Get status of all registered agents.
        
        Returns:
            List of agent status dicts
        """
        return self.agent_registry.get_all_agent_status()
    
    def is_running(self) -> bool:
        """Check if the server is running.
        
        Returns:
            True if server is running
        """
        return self._running
