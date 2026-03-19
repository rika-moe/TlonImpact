"""Tlon Client - Unified entry point for SDK.

Combines BaseClient (connection management) with ClientAdapter (client-type behavior)
to provide a simple, unified interface for all client types.
"""

from collections.abc import Callable
from typing import Any

from tlon.base.logger import get_logger

from .adapter import ClientAdapter, TerminalAdapter
from .base_client import BaseClient, ConnectionState

logger = get_logger(__name__)


class TlonClient:
    """Unified client for Tlon Server.

    TlonClient combines BaseClient (WebSocket connection management) with
    ClientAdapter (client-type-specific behavior) to provide a simple interface
    for connecting to the Tlon Server.

    Features:
    - Automatic connection management (connect, reconnect, disconnect)
    - Protocol version negotiation
    - Event filtering based on client type
    - REST API access
    - Async context manager support

    Usage:
        >>> from tlon.app.clients.sdk import TlonClient, TerminalAdapter
        >>>
        >>> # Using async context manager (recommended)
        >>> async with TlonClient("ws://localhost:8888/ws", adapter=TerminalAdapter()) as client:
        ...     await client.authenticate()
        ...     await client.send_message("Hello!")
        ...     # Events are automatically filtered by the adapter
        ...
        >>> # Or manual management
        >>> client = TlonClient("ws://localhost:8888/ws", adapter=TerminalAdapter())
        >>> await client.connect()
        >>> await client.authenticate()
        >>> await client.send_message("Hello!")
        >>> await client.disconnect()

    Args:
        ws_url: WebSocket server URL (e.g., "ws://localhost:8888/ws")
        adapter: Client adapter (TerminalAdapter, WebAdapter, etc.)
        rest_url: Optional REST API URL (auto-derived if not provided)
        auto_reconnect: Whether to auto-reconnect on disconnect
        reconnect_delay: Delay between reconnection attempts (seconds)

    Attributes:
        state: Current connection state
        session_id: Server-assigned session ID (after auth)
        protocol_version: Negotiated protocol version
    """

    def __init__(
        self,
        ws_url: str,
        adapter: ClientAdapter | None = None,
        rest_url: str | None = None,
        auto_reconnect: bool = True,
        reconnect_delay: float = 5.0,
    ):
        """Initialize the Tlon client.

        Args:
            ws_url: WebSocket server URL
            adapter: Client adapter (defaults to TerminalAdapter)
            rest_url: Optional REST API URL
            auto_reconnect: Whether to auto-reconnect
            reconnect_delay: Reconnection delay in seconds
        """
        self._adapter = adapter or TerminalAdapter()
        self._base = BaseClient(
            ws_url=ws_url,
            rest_url=rest_url,
            auto_reconnect=auto_reconnect,
            reconnect_delay=reconnect_delay,
        )

        # Set up event filtering through adapter
        self._base.on_event(self._on_event)

        # User event handlers (after adapter filtering)
        self._user_event_handlers: list[Callable[[dict[str, Any]], None]] = []

    # ==================== Properties ====================

    @property
    def state(self) -> ConnectionState:
        """Get current connection state."""
        return self._base.state

    @property
    def is_connected(self) -> bool:
        """Check if connected to server."""
        return self._base.is_connected

    @property
    def is_authenticated(self) -> bool:
        """Check if authenticated with server."""
        return self._base.is_authenticated

    @property
    def session_id(self) -> str | None:
        """Get server-assigned session ID."""
        return self._base.session_id

    @property
    def protocol_version(self) -> str | None:
        """Get negotiated protocol version."""
        return self._base.protocol_version

    @property
    def adapter(self) -> ClientAdapter:
        """Get the client adapter."""
        return self._adapter

    # ==================== Connection Management ====================

    async def connect(self) -> bool:
        """Connect to the server.

        Returns:
            True if connected successfully

        Raises:
            ConnectionError: If connection fails
        """
        return await self._base.connect()

    async def disconnect(self) -> None:
        """Disconnect from the server."""
        await self._base.disconnect()

    async def authenticate(self, timeout: float = 10.0) -> bool:
        """Authenticate with the server.

        Uses the adapter's client type and capabilities for authentication.

        Args:
            timeout: Authentication timeout in seconds

        Returns:
            True if authenticated successfully

        Raises:
            ConnectionError: If not connected
            AuthenticationError: If authentication fails
        """
        return await self._base.authenticate(
            client_type=self._adapter.client_type,
            capabilities=self._adapter.capabilities,
            metadata=self._adapter.get_auth_metadata(),
            timeout=timeout,
        )

    # ==================== Message Operations ====================

    async def send_message(
        self,
        content: str,
        mentions: list[str] | None = None,
        reply_to: str | None = None,
    ) -> bool:
        """Send a chat message.

        Args:
            content: Message content
            mentions: Optional list of agent IDs to mention
            reply_to: Optional message ID to reply to

        Returns:
            True if sent successfully
        """
        msg = {
            "type": "message",
            "content": content,
            "mentions": mentions or [],
        }
        if reply_to:
            msg["reply_to"] = reply_to

        return await self._base.send(msg)

    async def set_mode(self, mode: str) -> bool:
        """Set the operation mode (for TUI clients).

        Args:
            mode: Mode ("chat", "event", "agent")

        Returns:
            True if successful
        """
        # Update adapter mode if it's a TerminalAdapter
        if hasattr(self._adapter, "set_mode"):
            self._adapter.set_mode(mode)

        return await self._base.send(
            {
                "type": "mode",
                "mode": mode,
            }
        )

    async def set_filters(self, filters: dict[str, Any]) -> bool:
        """Set event filters.

        Args:
            filters: Filter dictionary, e.g.:
                - {"event_types": ["message.new", "agent.response"]}
                - {"trace_id": "trace-abc-123"}
                - {"sources": ["dev-001", "pm-001"]}

        Returns:
            True if successful
        """
        # Update adapter filters
        self._adapter.set_event_filters(filters)

        return await self._base.send(
            {
                "type": "filter",
                "filters": filters,
            }
        )

    async def set_trace_filter(self, trace_id: str | None) -> bool:
        """Filter events to show only those for a specific trace.

        This is a convenience method for setting trace-based filtering.
        Use set_trace_filter(None) to clear the filter.

        Args:
            trace_id: Trace ID to filter by, or None to clear

        Returns:
            True if successful
        """
        self._adapter.set_trace_filter(trace_id)

        return await self._base.send(
            {
                "type": "filter",
                "filters": {"trace_id": trace_id} if trace_id else {},
            }
        )

    # ==================== Event Handling ====================

    def on_event(self, handler: Callable[[dict[str, Any]], None]) -> Callable:
        """Register an event handler.

        Events are filtered by the adapter before being passed to the handler.

        Args:
            handler: Callback function for events

        Returns:
            The handler (for use as decorator)
        """
        self._user_event_handlers.append(handler)
        return handler

    def _on_event(self, event: dict[str, Any]) -> None:
        """Internal event handler - filters through adapter."""
        event_type = event.get("type", "")
        payload = event.get("payload", {})

        # Check if adapter wants this event
        if not self._adapter.should_receive_event(event_type, payload):
            return

        for handler in self._user_event_handlers:
            try:
                handler(event)
            except Exception as e:
                logger.error(f"User event handler error: {e}")

    def on_message(self, handler: Callable[[dict[str, Any]], None]) -> Callable:
        """Register a message handler.

        Args:
            handler: Callback function for messages

        Returns:
            The handler (for use as decorator)
        """
        return self._base.on_message(handler)

    def on_error(self, handler: Callable[[str], None]) -> Callable:
        """Register an error handler.

        Args:
            handler: Callback function for errors

        Returns:
            The handler (for use as decorator)
        """
        return self._base.on_error(handler)

    def on_state_change(self, handler: Callable[[ConnectionState], None]) -> Callable:
        """Register a state change handler.

        Args:
            handler: Callback function for state changes

        Returns:
            The handler (for use as decorator)
        """
        return self._base.on_state_change(handler)

    # ==================== REST API ====================

    async def get_history(self, limit: int = 100, before_id: str | None = None) -> list[dict]:
        """Get message history.

        Args:
            limit: Maximum number of messages
            before_id: Return messages before this ID

        Returns:
            List of messages
        """
        params = {"limit": limit}
        if before_id:
            params["before_id"] = before_id

        result = await self._base.rest_get("/history", params)
        return result.get("messages", []) if result else []

    async def get_agents(self) -> list[dict]:
        """Get all agents.

        Returns:
            List of agent info
        """
        result = await self._base.rest_get("/agents")
        return result.get("agents", []) if result else []

    async def get_agent(self, agent_id: str) -> dict | None:
        """Get a specific agent.

        Args:
            agent_id: Agent ID

        Returns:
            Agent info or None
        """
        result = await self._base.rest_get(f"/agents/{agent_id}")
        return result

    async def get_trace(self, trace_id: str) -> dict | None:
        """Get trace information.

        Args:
            trace_id: Trace ID

        Returns:
            Trace info or None
        """
        return await self._base.rest_get(f"/traces/{trace_id}")

    async def get_traces(self, limit: int = 100) -> list[dict]:
        """Get list of traces.

        Args:
            limit: Maximum number of traces

        Returns:
            List of trace summaries
        """
        result = await self._base.rest_get("/traces", {"limit": limit})
        return result.get("traces", []) if result else []

    async def get_agent_logs(self, agent_id: str) -> str | None:
        """Get agent logs.

        Args:
            agent_id: Agent ID

        Returns:
            Log content or None
        """
        result = await self._base.rest_get(f"/agents/{agent_id}/logs")
        return result.get("logs") if result else None

    # ==================== Context Manager ====================

    async def __aenter__(self) -> "TlonClient":
        """Async context manager entry.

        Connects to the server automatically.
        """
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit.

        Disconnects from the server automatically.
        """
        await self.disconnect()
