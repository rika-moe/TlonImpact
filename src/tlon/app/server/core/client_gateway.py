"""Tlon Server Core.

The Server is a long-running process that manages the core functionality:
- Agent lifecycle management
- Client session management

It exposes REST API for configuration and WebSocket for real-time updates.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Callable, Any

from tlon.base.events.event_bus import EventBus
from tlon.organization.messaging import Participant
from tlon.organization.messaging.store import MessageStore
from tlon.base.events.event import Event, EventType
from tlon.organization.messaging.message import Message
from tlon.organization.positions import PositionRegistry
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


class ClientGateway:
    """Main server class for Tlon

    Responsibilities:
    - Client session management
    - Message history access
    - Participant list aggregation
    
    All core components (EventBus, MessageStore) are created
    by ServerApp and injected into ClientGateway.
    Agent lifecycle is managed directly by AgentRegistry.
    
    Example:
        >>> server = ClientGateway(
        ...     event_bus=event_bus,
        ...     message_store=message_store,
        ...     agent_registry=agent_registry,
        ...     config_manager=config_manager,
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
        message_store: MessageStore,
        agent_registry: Any,  # AgentRegistry
        position_registry: PositionRegistry,

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
            message_store: The MessageStore instance (injected)
            agent_registry: The AgentRegistry instance (injected)
            position_registry: Position registry for lookups (injected)

            config_manager: ConfigManager for reload support (injected)
            data_dir: Directory for persistence
            host: Host to bind the server to
            port: Port to listen on
        """
        # Store injected dependencies
        self._event_bus = event_bus
        self._message_store = message_store
        self._agent_registry = agent_registry
        self._position_registry = position_registry

        self._config_manager = config_manager
        
        self.host = host
        self.port = port
        self.data_dir = data_dir
        
        # Session management
        self.sessions: Dict[str, ClientSession] = {}
        self.session_counter = 0
        self._session_lock = asyncio.Lock()
        
        # Event subscription ID for receiving messages
        self._message_subscription: Optional[str] = None
        
        # Session event handlers (session_id -> handler)
        self._session_handlers: Dict[str, Callable[[Event], None]] = {}
        
        # Server state
        self._running = False
        self._shutdown_event = asyncio.Event()
        
        logger.info(f"ClientGateway initialized (host={host}, port={port})")
    
    async def initialize(self) -> None:
        """Initialize ClientGateway.
        
        Validates all dependencies and subscribes to EventBus for messages.
        """
        logger.info("Initializing ClientGateway...")
        
        # Validate dependencies (all must be non-null)
        assert self._event_bus is not None, "EventBus not injected"
        assert self._message_store is not None, "MessageStore not injected"
        assert self._agent_registry is not None, "AgentRegistry not injected"
        assert self._position_registry is not None, "PositionRegistry not injected"
        assert self._config_manager is not None, "ConfigManager not injected"
        
        # Subscribe to MESSAGE_NEW to push messages to clients
        self._message_subscription = self._event_bus.subscribe(
            EventType.MESSAGE_NEW,
            self._on_message_new
        )
        logger.info("Subscribed to MESSAGE_NEW events")
        
        logger.info("All dependencies validated")
        logger.info("ClientGateway initialized")
    
    # ========== Property Accessors ==========
    
    @property
    def event_bus(self) -> EventBus:
        """Access the injected EventBus."""
        return self._event_bus
    
    @property
    def message_store(self) -> MessageStore:
        """Access the injected MessageStore."""
        return self._message_store
    
    @property
    def agent_registry(self) -> Any:
        """Access the injected AgentRegistry."""
        return self._agent_registry
    
    @property
    def position_registry(self) -> PositionRegistry:
        """Access the injected PositionRegistry."""
        return self._position_registry
    
    def get_all_participants(self) -> List[Participant]:
        """Get all participants by aggregating from AgentRegistry and sessions.
        
        This replaces the old Congress-based participant management with
        a real-time aggregation approach.
        
        Returns:
            List of all participants (agents + connected humans)
        """
        participants: List[Participant] = []
        
        # Add all agents from AgentRegistry
        for agent_id in self._agent_registry.get_all():
            config = self._agent_registry.get_config(agent_id)
            if config:
                participants.append(Participant(
                    id=agent_id,
                    name=config.name,
                    type="agent",
                    metadata={"position_id": config.position_id}
                ))
        
        # Add human if there are connected sessions
        if self.sessions:
            participants.append(Participant(
                id="human",
                name="人类",
                type="human"
            ))
        
        return participants
    
    def get_participant_names(self) -> List[str]:
        """Get names of all participants.
        
        Returns:
            List of participant names
        """
        return [p.name for p in self.get_all_participants()]

    async def start(self) -> None:
        """Start the server.
        
        This method blocks until the server is shut down.
        """
        self._running = True
        logger.info(f"ClientGateway started on {self.host}:{self.port}")
        
        # Wait for shutdown signal
        await self._shutdown_event.wait()
        
        logger.info("ClientGateway stopping...")
    
    async def shutdown(self) -> None:
        """Shutdown the server gracefully.
        
        Note: Core components are managed by ServerApp, ClientGateway only
        handles client disconnections and state cleanup.
        """
        if not self._running:
            return
        
        logger.info("Shutting down ClientGateway...")
        self._running = False
        self._shutdown_event.set()
        
        # Disconnect all clients
        async with self._session_lock:
            session_ids = list(self.sessions.keys())
        
        for session_id in session_ids:
            await self.disconnect_client(session_id)
        
        # Note: Component shutdown is handled by ServerApp (who created them)
        # We only clear our references
        logger.info("ClientGateway shutdown complete")
    
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
            # Store the event handler for this session
            self._session_handlers[session_id] = event_handler
        
        logger.info(f"Client connected: {session_id} ({client_type})")
        return session
    
    async def _on_message_new(self, event: Event) -> None:
        """Handle MESSAGE_NEW event from EventBus.
        
        Pushes messages to all connected client sessions.
        
        Args:
            event: The MESSAGE_NEW event containing the message
        """
        message: Message = event.payload
        
        # Push to all connected sessions
        async with self._session_lock:
            sessions = list(self.sessions.items())
        
        for session_id, session in sessions:
            # Check if this session should receive this message
            if session.mode == "agent":
                continue  # Agent mode - don't send messages
            
            # Apply custom filters if set
            if session.filters:
                if "agent" in session.filters:
                    agent_filter = session.filters["agent"]
                    source = message.sender_id
                    if not self._match_wildcard(source, agent_filter):
                        continue
            
            # Push to this session
            await self._push_to_session(session_id, event)
    
    async def _push_to_session(self, session_id: str, event: Event) -> None:
        """Push an event to a specific session.
        
        Args:
            session_id: The target session ID
            event: The event to push
        """
        handler = self._session_handlers.get(session_id)
        if not handler:
            return
        
        try:
            if asyncio.iscoroutinefunction(handler):
                await handler(event)
            else:
                handler(event)
        except Exception as e:
            logger.error(f"[Server] Error pushing event to session {session_id}: {e}")
    
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
    
    async def disconnect_client(self, session_id: str) -> bool:
        """Disconnect a client session.
        
        Args:
            session_id: The session ID to disconnect
            
        Returns:
            True if session was found and removed
        """
        async with self._session_lock:
            session = self.sessions.pop(session_id, None)
            # Remove event handler
            self._session_handlers.pop(session_id, None)
        
        if session:
            logger.info(f"Client disconnected: {session_id}")
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
        
        Publishes message via EventBus. MessageRouter will store it and
        other components will receive it.
        
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
        
        # Publish via EventBus - MessageRouter will store, ClientGateway will push to clients
        await self._event_bus.publish(Event(
            type=EventType.MESSAGE_NEW,
            payload=message,
            source=sender_id,
        ))
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
        return await self._message_store.get_history(limit, before_id)
    
    def is_running(self) -> bool:
        """Check if the server is running.
        
        Returns:
            True if server is running
        """
        return self._running
