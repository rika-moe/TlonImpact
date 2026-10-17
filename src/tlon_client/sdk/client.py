"""Tlon Client - Unified entry point for SDK.

Combines BaseClient (REST + SSE transport, protocol v2) with ClientAdapter
(client-type behavior) to provide a simple, unified interface for all
client types.
"""

from collections.abc import Callable
from types import TracebackType
from typing import Any
from uuid import uuid4

import structlog

from .adapter import ClientAdapter, TerminalAdapter
from .base_client import BaseClient, ConnectionState

logger = structlog.get_logger(__name__)


class TlonClient:
    """Unified client for Tlon Server.

    TlonClient combines BaseClient (REST + SSE transport) with
    ClientAdapter (client-type-specific behavior) to provide a simple
    interface for connecting to the Tlon Server.

    Features:
    - Automatic connection management (connect, reconnect, disconnect)
    - Server-side event filtering (declared by the adapter)
    - Session snapshot (participants/agents) from POST /sessions
    - REST API access
    - Async context manager support

    Usage:
        >>> from tlon_client.sdk import TlonClient, TerminalAdapter
        >>>
        >>> # Using async context manager (recommended)
        >>> async with TlonClient("http://localhost:8080", adapter=TerminalAdapter()) as client:
        ...     await client.authenticate(username="alice")
        ...     await client.send_message("Hello!", to=["agent-1"])
        ...     # Events are filtered by the server per the adapter's declarations
        ...
        >>> # Or manual management
        >>> client = TlonClient("http://localhost:8080", adapter=TerminalAdapter())
        >>> await client.connect()
        >>> await client.authenticate()
        >>> await client.send_message("Hello!", to=["agent-1"])
        >>> await client.disconnect()

    Args:
        base_url: REST API base URL (e.g., "http://localhost:8080")
        adapter: Client adapter (defaults to TerminalAdapter)
        auto_reconnect: Whether to auto-reconnect on stream loss
        reconnect_delay: Base delay between reconnection attempts (seconds,
            grows exponentially)
        max_reconnect_attempts: Maximum reconnection attempts (0 = unlimited)

    Attributes:
        state: Current connection state
        session_id: Server-assigned session ID (after authenticate)
        protocol_version: Protocol version in use ("2.0.0")
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8080",
        adapter: ClientAdapter | None = None,
        auto_reconnect: bool = True,
        reconnect_delay: float = 5.0,
        max_reconnect_attempts: int = 10,
    ):
        """Initialize the Tlon client.

        Args:
            base_url: REST API base URL
            adapter: Client adapter (defaults to TerminalAdapter)
            auto_reconnect: Whether to auto-reconnect
            reconnect_delay: Base reconnection delay in seconds
            max_reconnect_attempts: Maximum reconnection attempts (0 = unlimited)
        """
        self._adapter = adapter or TerminalAdapter()
        self._base = BaseClient(
            base_url=base_url,
            auto_reconnect=auto_reconnect,
            reconnect_delay=reconnect_delay,
            max_reconnect_attempts=max_reconnect_attempts,
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
        """Check if a session is established with the server."""
        return self._base.is_authenticated

    @property
    def session_id(self) -> str | None:
        """Get server-assigned session ID."""
        return self._base.session_id

    @property
    def protocol_version(self) -> str | None:
        """Get the protocol version in use."""
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

    async def authenticate(self, username: str | None = None, timeout: float = 10.0) -> bool:
        """Establish a session with the server (POST /sessions).

        Uses the adapter's client type and mode for the session request,
        and declares the adapter's effective filters so the server (the
        single filtering point) can enforce them for the whole session.

        Args:
            username: Optional human username — determines the
                "human:<username>" identity this session speaks as
                (server defaults to "guest" when omitted)
            timeout: Session establishment timeout in seconds

        Returns:
            True if the session was established successfully

        Raises:
            ConnectionError: If not connected
            AuthenticationError: If the session request fails
        """
        return await self._base.authenticate(
            client_type=self._adapter.client_type,
            capabilities=self._adapter.capabilities,
            metadata=self._adapter.get_auth_metadata(),
            timeout=timeout,
            filters=self._adapter.get_effective_filters(),
            username=username,
            mode=getattr(self._adapter, "mode", "chat"),
        )

    # ==================== Message Operations ====================

    async def send_message(
        self,
        content: str,
        to: list[str],
        cc: list[str] | None = None,
        subject: str | None = None,
        reply_to: str | None = None,
        thread_id: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """Send a mail message (POST /messages, protocol v2).

        Args:
            content: Message content
            to: Recipients (names, ids or full addresses); explicit
                addressing is the only entry point since PR-C removed
                the ``mentions`` alias and the ``@名字`` content bridge
            cc: Optional carbon-copy recipients
            subject: Optional mail subject
            reply_to: Optional message ID to reply to
            request_id: Stable key for retries; omitted keys are generated once.
            thread_id: Optional thread ID (inherit from the upstream
                mail when replying; otherwise the reply starts a new
                thread)

        Returns:
            Receipt separating local inbox delivery from gateway attempts.
        """
        request_id = str(uuid4()) if request_id is None else request_id
        msg: dict[str, Any] = {
            "request_id": request_id,
            "content": content,
            "to": to,
        }
        if self._base.username:
            msg["username"] = self._base.username
        if cc:
            msg["cc"] = cc
        if subject:
            msg["subject"] = subject
        if reply_to:
            msg["in_reply_to"] = reply_to
        if thread_id:
            msg["thread_id"] = thread_id

        return await self._base.submit_mail(msg)

    async def set_mode(self, mode: str) -> bool:
        """Set the operation mode (PATCH /sessions/{id}).

        Mode semantics are enforced server-side; the adapter only tracks the
        mode locally. The adapter's effective filters (which may depend on the
        mode, e.g. high-frequency exclusion in event mode) are re-synced in
        the same request.

        Args:
            mode: Mode ("chat", "event", "agent")

        Returns:
            True if successful
        """
        # Update adapter mode if it's a TerminalAdapter
        if hasattr(self._adapter, "set_mode"):
            self._adapter.set_mode(mode)

        return await self._base.update_session(
            mode=mode,
            filters=self._adapter.get_effective_filters(),
        )

    async def set_filters(self, filters: dict[str, Any]) -> bool:
        """Set event filters (PATCH /sessions/{id}).

        The filters are merged with the adapter's client-type defaults and
        sent to the server, which is the single filtering point.

        Args:
            filters: Filter dictionary, e.g.:
                - {"event_types": ["mail.delivered", "agent.response"]}
                - {"trace_id": "trace-abc-123"}
                - {"exclude_event_types": ["agent.llm_request"]}
                - {"agent": "dev-*"}

        Returns:
            True if successful
        """
        # Update adapter filters
        self._adapter.set_event_filters(filters)

        return await self._base.update_session(
            filters=self._adapter.get_effective_filters(),
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

        return await self._base.update_session(
            filters=self._adapter.get_effective_filters(),
        )

    # ==================== Event Handling ====================

    def on_event(
        self, handler: Callable[[dict[str, Any]], None]
    ) -> Callable[[dict[str, Any]], None]:
        """Register an event handler.

        Events are pre-filtered by the server (the single filtering point)
        according to the session's mode and declared filters; every event
        received here is passed to the handler as-is.

        Args:
            handler: Callback function for events

        Returns:
            The handler (for use as decorator)
        """
        self._user_event_handlers.append(handler)
        return handler

    def _on_event(self, event: dict[str, Any]) -> None:
        """Internal event handler - dispatches server-filtered events."""
        for handler in self._user_event_handlers:
            try:
                handler(event)
            except Exception as e:
                logger.error("user_event_handler_error", error=str(e), exc_info=True)

    def on_message(
        self, handler: Callable[[dict[str, Any]], None]
    ) -> Callable[[dict[str, Any]], None]:
        """Register a message handler.

        Args:
            handler: Callback function for messages

        Returns:
            The handler (for use as decorator)
        """
        return self._base.on_message(handler)

    def on_error(self, handler: Callable[[str], None]) -> Callable[[str], None]:
        """Register an error handler.

        Args:
            handler: Callback function for errors

        Returns:
            The handler (for use as decorator)
        """
        return self._base.on_error(handler)

    def on_state_change(
        self, handler: Callable[[ConnectionState], None]
    ) -> Callable[[ConnectionState], None]:
        """Register a state change handler.

        Args:
            handler: Callback function for state changes

        Returns:
            The handler (for use as decorator)
        """
        return self._base.on_state_change(handler)

    def on_reconnect(self, handler: Callable[[], None]) -> Callable[[], None]:
        """Register a reconnect handler.

        Called after a successful auto-reconnect (the session has been
        re-established via POST /sessions and the SSE stream reopened),
        so clients can restore session state (mode, history, etc.). Not
        called for the initial connect.

        Args:
            handler: Callback function (sync or async) with no arguments

        Returns:
            The handler (for use as decorator)
        """
        return self._base.on_reconnect(handler)

    # ==================== REST API ====================

    async def get_agents(self) -> list[dict[str, Any]]:
        """Get all agents.

        Returns:
            List of agent info
        """
        result = await self._base.rest_get("/agents")
        return result.get("agents", []) if result else []

    async def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        """Get a specific agent.

        Args:
            agent_id: Agent ID

        Returns:
            Agent info or None
        """
        result = await self._base.rest_get(f"/agents/{agent_id}")
        return result

    async def get_trace(self, trace_id: str) -> dict[str, Any] | None:
        """Get trace information.

        Args:
            trace_id: Trace ID

        Returns:
            Trace info or None
        """
        return await self._base.rest_get(f"/traces/{trace_id}")

    async def get_traces(self, limit: int = 100) -> list[dict[str, Any]]:
        """Get list of traces.

        Args:
            limit: Maximum number of traces

        Returns:
            List of trace summaries
        """
        result = await self._base.rest_get("/traces", {"limit": limit})
        return result.get("traces", []) if result else []

    async def get_inbox(self, username: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """Get a human member's mailbox (GET /inbox).

        Args:
            username: Human username — the inbox of "human:<username>" is
                served (server defaults to "guest" when omitted)
            limit: Maximum number of mails (server default 50, max 200)

        Returns:
            List of mail dicts (id, from_address, to, cc, subject, content,
            in_reply_to, thread_id, forward_of, timestamp, read), inbox and
            sent merged in chronological order
        """
        params: dict[str, Any] = {"limit": limit}
        if username is not None:
            params["username"] = username
        result = await self._base.rest_get("/inbox", params)
        return result.get("mails", []) if result else []

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

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Async context manager exit.

        Disconnects from the server automatically.
        """
        await self.disconnect()
