"""Tlon Server Client Gateway.

ClientGateway serves as a Facade (外观模式) providing a unified interface
to the client-facing subsystems:
- Session management (via SessionManager)
- Event notification (via NotificationService)
- Member discovery (via MemberDirectory)

This simplifies the API layer (REST + SSE handlers) by providing
a single entry point while delegating to specialized components internally.

Architecture:
    ClientGateway is a Component that depends on:
    - SessionManager (session lifecycle)
    - NotificationService (event pushing)
    - MemberDirectory (member aggregation)
    - ConfigManager (for config reload support)

    All dependencies are injected via constructor (DAG rule).
"""

import asyncio
from pathlib import Path
from typing import Any

from tlon.api.members import MemberInfo
from tlon.base.component import Component
from tlon.base.logger import get_logger

from .member_directory import MemberDirectory
from .notification_service import EventHandler, NotificationService
from .session_manager import ClientSession, SessionManager

logger = get_logger(__name__)


class ClientGateway(Component):
    """Client Gateway - Facade for client-facing operations.

    The Gateway provides a simplified, unified interface for:
    - Client session lifecycle (connect/disconnect)
    - Member listing and discovery
    - Event subscription for real-time updates

    Internally, it coordinates three specialized components:
    - SessionManager: Session lifecycle management
    - NotificationService: Event pushing to clients
    - MemberDirectory: Member aggregation

    Dependencies (constructor injected):
        - session_manager: SessionManager
        - notification_service: NotificationService
        - member_directory: MemberDirectory

    Example:
        >>> gateway = ClientGateway(
        ...     session_manager=session_manager,
        ...     notification_service=notification_service,
        ...     member_directory=member_directory,
        ...     data_dir=data_dir,
        ...     host=host,
        ...     port=port,
        ... )
        >>> await gateway.initialize()
        >>> session = await gateway.open_session("tui")
        >>> members = gateway.get_all_members()
    """

    def __init__(
        self,
        *,
        # Core specialized components (injected via DI)
        session_manager: SessionManager,
        notification_service: NotificationService,
        member_directory: MemberDirectory,
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
            member_directory: Member discovery service
            data_dir: Directory for persistence
            host: Server host
            port: Server port
        """
        super().__init__(name="ClientGateway")

        # Store injected dependencies
        self._session_manager = session_manager
        self._notifications = notification_service
        self._member_directory = member_directory

        # Server configuration
        self.host = host
        self.port = port
        self.data_dir = data_dir

        # State
        self._running = False
        self._shutdown_event = asyncio.Event()

        logger.info("client_gateway_constructed", host=host, port=port)

    async def initialize(self) -> None:
        """Initialize the gateway and its sub-components.

        Starts the notification service to begin listening for events.
        """
        logger.info("client_gateway_initializing")

        # Initialize notification service
        await self._notifications.initialize()

        self._running = True
        self._initialized = True
        logger.info("client_gateway_initialized")

    async def shutdown(self) -> None:
        """Shutdown the gateway gracefully.

        Stops the notification service and cleans up resources.
        """
        if not self._running:
            return

        logger.info("client_gateway_shutdown_started")

        self._running = False
        self._shutdown_event.set()
        self._initialized = False

        # Shutdown notification service
        await self._notifications.shutdown()

        logger.info("client_gateway_shutdown_completed")

    # ==================== Session Management ====================

    async def open_session(
        self,
        client_type: str,
        metadata: dict[str, Any] | None = None,
        filters: dict[str, Any] | None = None,
    ) -> ClientSession:
        """Create a client session without an event handler.

        REST/SSE path (protocol v2): the session is established by
        ``POST /sessions``; the event handler is registered later, when
        the client opens its event stream (``GET /events``), via
        :meth:`set_event_handler`.

        Args:
            client_type: Type of client (e.g. "web")
            metadata: Optional client metadata
            filters: Optional initial event filters declared by the client

        Returns:
            The created client session
        """
        session = await self._session_manager.create_session(
            client_type=client_type, metadata=metadata, filters=filters
        )
        logger.info("client_connected", session_id=session.id, client_type=client_type)
        return session

    def set_event_handler(self, session_id: str, handler: EventHandler) -> None:
        """(Re)register the event handler for an existing session.

        Args:
            session_id: The session ID
            handler: Callback for events
        """
        self._notifications.register_handler(session_id, handler)

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
            logger.info("client_disconnected", session_id=session_id)

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

    # ==================== Member Operations ====================

    def get_all_members(self) -> list[MemberInfo]:
        """Get all members (agents + humans).

        Returns:
            List of all members
        """
        return self._member_directory.list_all()

    def get_member_names(self) -> list[str]:
        """Get names of all members.

        Returns:
            List of member names
        """
        return self._member_directory.get_member_names()

    def get_member(self, member_id: str) -> MemberInfo | None:
        """Get a specific member by ID.

        Args:
            member_id: The member ID

        Returns:
            The member if found
        """
        return self._member_directory.get_member(member_id)

    # ==================== Server State ====================

    def is_running(self) -> bool:
        """Check if the gateway is running.

        Returns:
            True if running
        """
        return self._running
