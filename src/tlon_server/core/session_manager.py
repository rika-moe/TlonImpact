"""Session Manager for Tlon Server.

Manages client session lifecycle with single responsibility.
Extracted from the original ClientGateway to follow SRP.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

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
    filters: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class SessionManager:
    """Manages client session lifecycle.

    Responsibilities:
    - Session creation and deletion
    - Session lookup and listing
    - Session state management (mode, filters)

    Does NOT handle:
    - Message routing
    - Event pushing
    - Member aggregation

    Example:
        >>> manager = SessionManager()
        >>> session = await manager.create_session("tui")
        >>> session = manager.get_session(session.id)
        >>> await manager.close_session(session.id)
    """

    def __init__(self) -> None:
        """Initialize the session manager."""
        self._sessions: dict[str, ClientSession] = {}
        self._counter = 0
        self._lock = asyncio.Lock()

        logger.info("session_manager_initialized")

    async def create_session(
        self,
        client_type: str,
        metadata: dict[str, Any] | None = None,
        filters: dict[str, Any] | None = None,
    ) -> ClientSession:
        """Create a new client session.

        Args:
            client_type: Type of client ("tui", "web", "telegram")
            metadata: Optional session metadata
            filters: Optional initial event filters (declared by the client,
                e.g. client-type default exclusions)

        Returns:
            The created client session
        """
        async with self._lock:
            self._counter += 1
            session_id = f"{client_type}-{self._counter:04d}"

            session = ClientSession(
                id=session_id,
                client_type=client_type,
                connected_at=datetime.now().isoformat(),
                filters=dict(filters) if filters else {},
                metadata=metadata or {},
            )

            self._sessions[session_id] = session

        logger.info("session_created", session_id=session_id, client_type=client_type)
        return session

    async def close_session(self, session_id: str) -> bool:
        """Close and remove a session.

        Args:
            session_id: The session ID to close

        Returns:
            True if session was found and removed
        """
        async with self._lock:
            session = self._sessions.pop(session_id, None)

        if session:
            logger.info("session_closed", session_id=session_id)
            return True

        return False

    def get_session(self, session_id: str) -> ClientSession | None:
        """Get a session by ID.

        Args:
            session_id: The session ID

        Returns:
            The session or None if not found
        """
        return self._sessions.get(session_id)

    def get_all_sessions(self) -> list[ClientSession]:
        """Get all active sessions.

        Returns:
            List of all active sessions
        """
        return list(self._sessions.values())

    def get_session_count(self) -> int:
        """Get the number of active sessions.

        Returns:
            Number of active sessions
        """
        return len(self._sessions)

    def update_session_mode(self, session_id: str, mode: str) -> bool:
        """Update a session's operation mode.

        Args:
            session_id: The session ID
            mode: New mode ("chat", "event", "agent")

        Returns:
            True if session was found and updated
        """
        session = self._sessions.get(session_id)
        if session:
            session.mode = mode
            logger.debug("session_mode_updated", session_id=session_id, mode=mode)
            return True
        return False

    def update_session_filters(self, session_id: str, filters: dict[str, Any]) -> bool:
        """Update a session's event filters.

        Args:
            session_id: The session ID
            filters: New filters

        Returns:
            True if session was found and updated
        """
        session = self._sessions.get(session_id)
        if session:
            session.filters = filters
            logger.debug("session_filters_updated", session_id=session_id, filters=filters)
            return True
        return False
