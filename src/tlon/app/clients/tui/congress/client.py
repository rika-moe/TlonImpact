"""Congress WebSocket Client.

Provides a WebSocket client specifically designed for the Congress TUI,
with automatic reconnection, heartbeat, and message protocol handling.
"""

import asyncio
import contextlib
from collections.abc import Callable
from enum import Enum
from typing import Any

from tlon.app.clients.sdk import ConnectionState, TerminalAdapter, TlonClient
from tlon.app.clients.tui.congress.state import (
    AgentInfo,
    AgentState,
    CongressState,
    Message,
    MessageType,
)
from tlon.base.logger import get_logger

logger = get_logger(__name__)


class ClientConnectionState(Enum):
    """Extended connection state for Congress client."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    AUTHENTICATING = "authenticating"
    AUTHENTICATED = "authenticated"
    RECONNECTING = "reconnecting"
    DISCONNECTING = "disconnecting"
    ERROR = "error"


class CongressWebSocketClient:
    """WebSocket client for Congress TUI.

    Wraps TlonClient with Congress-specific functionality:
    - Automatic reconnection with exponential backoff
    - Connection state management
    - Heartbeat/ping-pong handling
    - Message protocol processing
    - Event handling for agent typing, messages, etc.

    Attributes:
        state: CongressState instance for UI state
        connection_state: Current WebSocket connection state
        ws_url: WebSocket server URL
        rest_url: REST API URL

    Example:
        >>> client = CongressWebSocketClient("ws://localhost:8888/ws")
        >>> await client.connect()
        >>> await client.send_message("Hello!")
        >>> await client.disconnect()
    """

    # Exponential backoff configuration
    INITIAL_RECONNECT_DELAY = 1.0
    MAX_RECONNECT_DELAY = 30.0
    RECONNECT_BACKOFF_FACTOR = 2.0
    MAX_RECONNECT_ATTEMPTS = 10

    def __init__(
        self,
        ws_url: str = "ws://localhost:8888/ws",
        rest_url: str | None = None,
        auto_reconnect: bool = True,
    ):
        """Initialize Congress WebSocket client.

        Args:
            ws_url: WebSocket server URL
            rest_url: Optional REST API URL
            auto_reconnect: Whether to auto-reconnect on disconnect
        """
        self.ws_url = ws_url
        self.rest_url = rest_url
        self.auto_reconnect = auto_reconnect

        # State management
        self.state = CongressState()
        self._connection_state = ClientConnectionState.DISCONNECTED

        # SDK client
        self._adapter = TerminalAdapter()
        self._sdk: TlonClient | None = None

        # Reconnection state
        self._reconnect_delay = self.INITIAL_RECONNECT_DELAY
        self._reconnect_attempts = 0
        self._reconnect_task: asyncio.Task | None = None
        self._should_stop = False

        # Event handlers
        self._message_handlers: list[Callable[[Message], None]] = []
        self._typing_handlers: list[Callable[[str, bool], None]] = []
        self._agent_handlers: list[Callable[[AgentInfo], None]] = []
        self._connection_handlers: list[Callable[[ClientConnectionState], None]] = []
        self._error_handlers: list[Callable[[str], None]] = []

        # Initialize SDK client
        self._init_sdk()

    def _init_sdk(self) -> None:
        """Initialize the underlying SDK client."""
        self._sdk = TlonClient(
            ws_url=self.ws_url,
            adapter=self._adapter,
            rest_url=self.rest_url,
            auto_reconnect=False,  # We handle reconnection ourselves
        )

        # Set up SDK event handlers
        self._sdk.on_event(self._on_sdk_event)
        self._sdk.on_message(self._on_sdk_message)
        self._sdk.on_error(self._on_sdk_error)
        self._sdk.on_state_change(self._on_sdk_state_change)

    # ==================== Properties ====================

    @property
    def connection_state(self) -> ClientConnectionState:
        """Get current connection state."""
        return self._connection_state

    @property
    def is_connected(self) -> bool:
        """Check if connected and authenticated."""
        return self._connection_state == ClientConnectionState.AUTHENTICATED

    @property
    def is_connecting(self) -> bool:
        """Check if currently connecting."""
        return self._connection_state in (
            ClientConnectionState.CONNECTING,
            ClientConnectionState.RECONNECTING,
        )

    # ==================== Connection Management ====================

    async def connect(self) -> bool:
        """Connect to the WebSocket server.

        Returns:
            True if connected and authenticated successfully
        """
        if self.is_connected or self.is_connecting:
            logger.debug("Already connected or connecting")
            return self.is_connected

        self._set_connection_state(ClientConnectionState.CONNECTING)
        self._should_stop = False

        try:
            if not self._sdk:
                self._init_sdk()

            # Connect
            connected = await self._sdk.connect()
            if not connected:
                logger.error("Failed to connect to server")
                self._set_connection_state(ClientConnectionState.ERROR)
                return False

            # Authenticate
            self._set_connection_state(ClientConnectionState.AUTHENTICATING)
            authenticated = await self._sdk.authenticate()
            if not authenticated:
                logger.error("Failed to authenticate")
                self._set_connection_state(ClientConnectionState.ERROR)
                return False

            # Set mode to chat for Congress TUI
            await self._sdk.set_mode("chat")

            # Reset reconnection state on successful connection
            self._reconnect_delay = self.INITIAL_RECONNECT_DELAY
            self._reconnect_attempts = 0

            self._set_connection_state(ClientConnectionState.AUTHENTICATED)
            logger.info("Connected and authenticated successfully")

            # Load initial data
            await self._load_initial_data()

            return True

        except Exception as e:
            logger.error(f"Connection error: {e}")
            self._set_connection_state(ClientConnectionState.ERROR)
            if self.auto_reconnect:
                self._start_reconnect()
            return False

    async def disconnect(self) -> None:
        """Disconnect from the server gracefully."""
        self._should_stop = True
        self._set_connection_state(ClientConnectionState.DISCONNECTING)

        # Cancel any pending reconnection
        if self._reconnect_task:
            self._reconnect_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reconnect_task
            self._reconnect_task = None

        # Disconnect SDK
        if self._sdk:
            await self._sdk.disconnect()

        self._set_connection_state(ClientConnectionState.DISCONNECTED)
        logger.info("Disconnected from server")

    def _set_connection_state(self, state: ClientConnectionState) -> None:
        """Set connection state and notify handlers."""
        old_state = self._connection_state
        self._connection_state = state
        self.state.connection_state = state.value

        if old_state != state:
            logger.debug(f"Connection state changed: {old_state.value} -> {state.value}")
            for handler in self._connection_handlers:
                try:
                    if asyncio.iscoroutinefunction(handler):
                        asyncio.create_task(handler(state))
                    else:
                        handler(state)
                except Exception as e:
                    logger.error(f"Connection handler error: {e}")

    # ==================== Reconnection ====================

    def _on_sdk_state_change(self, state: ConnectionState) -> None:
        """Handle SDK state changes."""
        logger.debug(f"SDK state changed: {state.value}")

        # Map SDK states to our states
        state_map = {
            ConnectionState.DISCONNECTED: ClientConnectionState.DISCONNECTED,
            ConnectionState.CONNECTING: ClientConnectionState.CONNECTING,
            ConnectionState.CONNECTED: ClientConnectionState.CONNECTED,
            ConnectionState.AUTHENTICATING: ClientConnectionState.AUTHENTICATING,
            ConnectionState.AUTHENTICATED: ClientConnectionState.AUTHENTICATED,
            ConnectionState.RECONNECTING: ClientConnectionState.RECONNECTING,
            ConnectionState.ERROR: ClientConnectionState.ERROR,
        }

        new_state = state_map.get(state, ClientConnectionState.ERROR)

        # Handle disconnection for auto-reconnect
        if (
            state == ConnectionState.DISCONNECTED
            and self.auto_reconnect
            and not self._should_stop
            and self._connection_state == ClientConnectionState.AUTHENTICATED
        ):
            logger.info("Connection lost, starting reconnection")
            self._start_reconnect()
            return

        self._set_connection_state(new_state)

    def _start_reconnect(self) -> None:
        """Start reconnection process with exponential backoff."""
        if self._reconnect_task and not self._reconnect_task.done():
            return

        self._set_connection_state(ClientConnectionState.RECONNECTING)
        self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def _reconnect_loop(self) -> None:
        """Reconnection loop with exponential backoff."""
        while not self._should_stop and self.auto_reconnect:
            if self._reconnect_attempts >= self.MAX_RECONNECT_ATTEMPTS:
                logger.error(f"Max reconnection attempts ({self.MAX_RECONNECT_ATTEMPTS}) reached")
                self._set_connection_state(ClientConnectionState.ERROR)
                break

            self._reconnect_attempts += 1
            logger.info(
                f"Reconnecting in {self._reconnect_delay}s "
                f"(attempt {self._reconnect_attempts}/{self.MAX_RECONNECT_ATTEMPTS})"
            )

            await asyncio.sleep(self._reconnect_delay)

            # Try to connect
            try:
                success = await self.connect()
                if success:
                    logger.info("Reconnected successfully")
                    return
            except Exception as e:
                logger.warning(f"Reconnection attempt failed: {e}")

            # Increase delay for next attempt (exponential backoff)
            self._reconnect_delay = min(
                self._reconnect_delay * self.RECONNECT_BACKOFF_FACTOR,
                self.MAX_RECONNECT_DELAY,
            )

    # ==================== Message Handling ====================

    def _on_sdk_event(self, event: dict[str, Any]) -> None:
        """Handle SDK events."""
        event_type = event.get("type", "")
        payload = event.get("payload", {})

        logger.debug(f"Received event: {event_type}")

        # Handle different event types
        if event_type == "message.new":
            self._handle_message_new(payload)
        elif event_type == "agent.typing":
            self._handle_agent_typing(payload)
        elif event_type == "agent.response":
            self._handle_agent_response(payload)
        elif event_type == "agent.started":
            self._handle_agent_started(payload)
        elif event_type == "agent.stopped":
            self._handle_agent_stopped(payload)
        elif event_type == "system.message":
            self._handle_system_message(payload)

    def _on_sdk_message(self, msg: dict[str, Any]) -> None:
        """Handle SDK messages (non-event types)."""
        msg_type = msg.get("type", "")

        if msg_type == "participants":
            self._handle_participants(msg.get("participants", []))
        elif msg_type == "history":
            self._handle_history(msg.get("messages", []))
        elif msg_type == "pong":
            # Heartbeat response received
            logger.debug("Received pong")

    def _on_sdk_error(self, error_msg: str) -> None:
        """Handle SDK errors."""
        logger.error(f"SDK error: {error_msg}")
        for handler in self._error_handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    asyncio.create_task(handler(error_msg))
                else:
                    handler(error_msg)
            except Exception as e:
                logger.error(f"Error handler error: {e}")

    def _handle_message_new(self, payload: dict[str, Any]) -> None:
        """Handle new message event."""
        message = Message.from_dict(payload)
        self.state.add_message(message)

        for handler in self._message_handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    asyncio.create_task(handler(message))
                else:
                    handler(message)
            except Exception as e:
                logger.error(f"Message handler error: {e}")

    def _handle_agent_typing(self, payload: dict[str, Any]) -> None:
        """Handle agent typing event."""
        agent_id = payload.get("agent_id", "")
        is_typing = payload.get("typing", True)

        self.state.set_typing(agent_id, is_typing)

        for handler in self._typing_handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    asyncio.create_task(handler(agent_id, is_typing))
                else:
                    handler(agent_id, is_typing)
            except Exception as e:
                logger.error(f"Typing handler error: {e}")

    def _handle_agent_response(self, payload: dict[str, Any]) -> None:
        """Handle agent response event."""
        # Agent response is treated as a new message
        self._handle_message_new(payload)

    def _handle_agent_started(self, payload: dict[str, Any]) -> None:
        """Handle agent started event."""
        agent_info = AgentInfo.from_dict(payload)
        agent_info.state = AgentState.ACTIVE
        self.state.update_agent(agent_info)

        for handler in self._agent_handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    asyncio.create_task(handler(agent_info))
                else:
                    handler(agent_info)
            except Exception as e:
                logger.error(f"Agent handler error: {e}")

    def _handle_agent_stopped(self, payload: dict[str, Any]) -> None:
        """Handle agent stopped event."""
        agent_id = payload.get("agent_id", "")
        if agent_id in self.state.agents:
            self.state.agents[agent_id].state = AgentState.OFFLINE

    def _handle_system_message(self, payload: dict[str, Any]) -> None:
        """Handle system message event."""
        message = Message(
            content=payload.get("content", ""),
            sender_type=MessageType.SYSTEM,
            sender_name="System",
            metadata=payload.get("metadata", {}),
        )
        self.state.add_message(message)

        for handler in self._message_handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    asyncio.create_task(handler(message))
                else:
                    handler(message)
            except Exception as e:
                logger.error(f"Message handler error: {e}")

    def _handle_participants(self, participants: list[dict]) -> None:
        """Handle participants list update."""
        for participant_data in participants:
            agent_info = AgentInfo.from_dict(participant_data)
            self.state.update_agent(agent_info)

    def _handle_history(self, messages: list[dict]) -> None:
        """Handle message history."""
        for msg_data in messages:
            message = Message.from_dict(msg_data)
            self.state.add_message(message)

    # ==================== Data Loading ====================

    async def _load_initial_data(self) -> None:
        """Load initial data after connection."""
        if not self._sdk:
            return

        try:
            # Load agents
            agents = await self._sdk.get_agents()
            for agent_data in agents:
                agent_info = AgentInfo.from_dict(agent_data)
                self.state.update_agent(agent_info)

            # Load history
            history = await self._sdk.get_history(limit=50)
            for msg_data in history:
                message = Message.from_dict(msg_data)
                self.state.add_message(message)

        except Exception as e:
            logger.warning(f"Failed to load initial data: {e}")

    # ==================== Sending Messages ====================

    async def send_message(
        self,
        content: str,
        mentions: list[str] | None = None,
        reply_to: str | None = None,
    ) -> bool:
        """Send a message to the server.

        Args:
            content: Message content
            mentions: Optional list of agent IDs to mention
            reply_to: Optional message ID to reply to

        Returns:
            True if sent successfully
        """
        if not self.is_connected:
            logger.error("Cannot send message: not connected")
            return False

        if not self._sdk:
            return False

        try:
            success = await self._sdk.send_message(
                content=content,
                mentions=mentions,
                reply_to=reply_to,
            )

            if success:
                # Add to input history
                self.state.add_input_to_history(content)

            return success

        except Exception as e:
            logger.error(f"Failed to send message: {e}")
            return False

    async def ping(self) -> bool:
        """Send a ping to check connection health.

        Returns:
            True if pong received
        """
        if not self.is_connected or not self._sdk:
            return False

        try:
            # Send ping through SDK
            return await self._sdk.send({"type": "ping"})
        except Exception as e:
            logger.warning(f"Ping failed: {e}")
            return False

    # ==================== Event Handler Registration ====================

    def on_message(self, handler: Callable[[Message], None]) -> Callable:
        """Register a message handler.

        Args:
            handler: Callback function for new messages

        Returns:
            The handler (for use as decorator)
        """
        self._message_handlers.append(handler)
        return handler

    def on_typing(self, handler: Callable[[str, bool], None]) -> Callable:
        """Register a typing state handler.

        Args:
            handler: Callback function(agent_id, is_typing)

        Returns:
            The handler (for use as decorator)
        """
        self._typing_handlers.append(handler)
        return handler

    def on_agent_update(self, handler: Callable[[AgentInfo], None]) -> Callable:
        """Register an agent update handler.

        Args:
            handler: Callback function for agent updates

        Returns:
            The handler (for use as decorator)
        """
        self._agent_handlers.append(handler)
        return handler

    def on_connection_change(self, handler: Callable[[ClientConnectionState], None]) -> Callable:
        """Register a connection state change handler.

        Args:
            handler: Callback function for connection state changes

        Returns:
            The handler (for use as decorator)
        """
        self._connection_handlers.append(handler)
        return handler

    def on_error(self, handler: Callable[[str], None]) -> Callable:
        """Register an error handler.

        Args:
            handler: Callback function for errors

        Returns:
            The handler (for use as decorator)
        """
        self._error_handlers.append(handler)
        return handler

    # ==================== Context Manager ====================

    async def __aenter__(self) -> "CongressWebSocketClient":
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
