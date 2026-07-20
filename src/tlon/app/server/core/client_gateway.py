"""Tlon Server Client Gateway.

ClientGateway serves as a Facade (外观模式) providing a unified interface
to the client-facing subsystems:
- Session management (via SessionManager)
- Event notification (via NotificationService)
- Participant discovery (via ParticipantDirectory)
- Message operations (via EventBus and MessageStore)

This simplifies the API layer (WebSocketHandler, RESTHandler) by providing
a single entry point while delegating to specialized components internally.

Architecture:
    ClientGateway is a Component that depends on:
    - SessionManager (session lifecycle)
    - NotificationService (event pushing)
    - ParticipantDirectory (participant aggregation)
    - EventBus (for message publishing)
    - MessageStore (for history retrieval)
    - ConfigManager (for config reload support)

    All dependencies are injected via constructor (DAG rule).
"""

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tlon.base.component import Component
from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus
from tlon.base.logger import get_logger
from tlon.organization.messaging.message import Message
from tlon.organization.messaging.participant import Participant
from tlon.organization.messaging.store import MessageStore

from .notification_service import NotificationService
from .participant_directory import ParticipantDirectory
from .session_manager import ClientSession, SessionManager

logger = get_logger(__name__)


class ClientGateway(Component):
    """Client Gateway - Facade for client-facing operations.

    The Gateway provides a simplified, unified interface for:
    - Client session lifecycle (connect/disconnect)
    - Message sending and history retrieval
    - Participant listing and discovery
    - Event subscription for real-time updates

    Internally, it coordinates three specialized components:
    - SessionManager: Session lifecycle management
    - NotificationService: Event pushing to clients
    - ParticipantDirectory: Participant aggregation

    Dependencies (constructor injected):
        - session_manager: SessionManager
        - notification_service: NotificationService
        - participant_directory: ParticipantDirectory
        - event_bus: EventBus
        - message_store: MessageStore
        - config_manager: ConfigManager

    Example:
        >>> gateway = ClientGateway(
        ...     session_manager=session_manager,
        ...     notification_service=notification_service,
        ...     participant_directory=participant_directory,
        ...     event_bus=event_bus,
        ...     message_store=message_store,
        ...     config_manager=config_manager,
        ...     data_dir=data_dir,
        ...     host=host,
        ...     port=port,
        ... )
        >>> await gateway.initialize()
        >>> session = await gateway.connect_client("tui", handler)
        >>> await gateway.send_message("Hello")
        >>> participants = gateway.get_all_participants()
    """

    def __init__(
        self,
        *,
        # Core specialized components (injected via DI)
        session_manager: SessionManager,
        notification_service: NotificationService,
        participant_directory: ParticipantDirectory,
        # Additional dependencies
        event_bus: EventBus,
        message_store: MessageStore,
        config_manager: Any,  # ConfigManager for reload support
        # Configuration
        data_dir: Path,
        host: str,
        port: int,
    ):
        """Initialize the client gateway.

        All dependencies are injected via constructor (DAG architecture).

        Args:
            session_manager: Session lifecycle manager
            notification_service: Event notification service
            participant_directory: Participant discovery service
            event_bus: Event bus for message publishing
            message_store: Message storage
            config_manager: Configuration manager
            data_dir: Directory for persistence
            host: Server host
            port: Server port
        """
        super().__init__(name="ClientGateway")

        # Store injected dependencies
        self._session_manager = session_manager
        self._notifications = notification_service
        self._participants = participant_directory
        self._event_bus = event_bus
        self._message_store = message_store
        self._config_manager = config_manager

        # Server configuration
        self.host = host
        self.port = port
        self.data_dir = data_dir

        # State
        self._running = False
        self._shutdown_event = asyncio.Event()

        logger.info(f"ClientGateway constructed (host={host}, port={port})")

    async def initialize(self) -> None:
        """Initialize the gateway and its sub-components.

        Starts the notification service to begin listening for events.
        """
        logger.info("Initializing ClientGateway...")

        # Initialize notification service
        await self._notifications.initialize()

        self._running = True
        self._initialized = True
        logger.info("ClientGateway initialized successfully")

    async def shutdown(self) -> None:
        """Shutdown the gateway gracefully.

        Stops the notification service and cleans up resources.
        """
        if not self._running:
            return

        logger.info("Shutting down ClientGateway...")

        self._running = False
        self._shutdown_event.set()
        self._initialized = False

        # Shutdown notification service
        await self._notifications.shutdown()

        logger.info("ClientGateway shutdown complete")

    # ==================== Session Management ====================

    async def connect_client(
        self,
        client_type: str,
        event_handler: Callable[[Event], None],
        metadata: dict[str, Any] | None = None,
        filters: dict[str, Any] | None = None,
    ) -> ClientSession:
        """Connect a new client session.

        Creates a session and registers the event handler for real-time updates.

        Args:
            client_type: Type of client ("tui", "web", "telegram")
            event_handler: Callback for events
            metadata: Optional client metadata
            filters: Optional initial event filters declared by the client

        Returns:
            The created client session
        """
        # Create session via SessionManager
        session = await self._session_manager.create_session(
            client_type=client_type, metadata=metadata, filters=filters
        )

        # Register event handler via NotificationService
        self._notifications.register_handler(session.id, event_handler)

        logger.info(f"Client connected: {session.id} ({client_type})")
        return session

    async def disconnect_client(self, session_id: str) -> bool:
        """Disconnect a client session.

        Args:
            session_id: The session ID to disconnect

        Returns:
            True if session was found and removed
        """
        # Unregister handler first
        self._notifications.unregister_handler(session_id)

        # Close session
        result = await self._session_manager.close_session(session_id)

        if result:
            logger.info(f"Client disconnected: {session_id}")

        return result

    def get_session(self, session_id: str) -> ClientSession | None:
        """Get a session by ID.

        Args:
            session_id: The session ID

        Returns:
            The session or None if not found
        """
        return self._session_manager.get_session(session_id)

    def get_all_sessions(self) -> list[ClientSession]:
        """Get all active sessions.

        Returns:
            List of all sessions
        """
        return self._session_manager.get_all_sessions()

    async def update_session_mode(self, session_id: str, mode: str) -> bool:
        """Update a session's operation mode.

        Args:
            session_id: The session ID
            mode: New mode ("chat", "event", "agent")

        Returns:
            True if updated successfully
        """
        return self._session_manager.update_session_mode(session_id, mode)

    async def update_session_filters(self, session_id: str, filters: dict[str, Any]) -> bool:
        """Update a session's event filters.

        Args:
            session_id: The session ID
            filters: New filters

        Returns:
            True if updated successfully
        """
        return self._session_manager.update_session_filters(session_id, filters)

    # ==================== Message Operations ====================

    async def send_message(
        self,
        content: str,
        sender_id: str = "human",
        sender_name: str = "人类",
        sender_type: str = "human",
        content_type: str = "text",
        mentions: list[str] | None = None,
    ) -> Message:
        """Send a message to the chatroom.

        Publishes the message via EventBus. MessageRouter will handle
        routing, and NotificationService will push to clients.

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
            mentions=mentions or [],
        )

        await self._event_bus.publish(
            Event(
                type=EventType.MESSAGE_NEW,
                payload=message,
                source=sender_id,
            )
        )

        return message

    async def get_history(self, limit: int = 100, before_id: str | None = None) -> list[Message]:
        """Get chat history.

        Args:
            limit: Maximum number of messages
            before_id: Return messages before this ID

        Returns:
            List of messages
        """
        return await self._message_store.get_history(limit, before_id)

    # ==================== Participant Operations ====================

    def get_all_participants(self) -> list[Participant]:
        """Get all participants (agents + humans).

        Returns:
            List of all participants
        """
        return self._participants.list_all()

    def get_participant_names(self) -> list[str]:
        """Get names of all participants.

        Returns:
            List of participant names
        """
        return self._participants.get_participant_names()

    def get_participant(self, participant_id: str) -> Participant | None:
        """Get a specific participant by ID.

        Args:
            participant_id: The participant ID

        Returns:
            The participant if found
        """
        return self._participants.get_participant(participant_id)

    # ==================== Server State ====================

    def is_running(self) -> bool:
        """Check if the gateway is running.

        Returns:
            True if running
        """
        return self._running
