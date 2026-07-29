"""Congress WebSocket Client.

Provides a WebSocket client specifically designed for the Congress TUI,
with automatic reconnection, heartbeat, and message protocol handling.
"""

import asyncio
from collections.abc import Callable
from datetime import datetime
from enum import Enum
from typing import Any

from tlon.app.clients.sdk.adapter import TerminalAdapter
from tlon.app.clients.sdk.base_client import ConnectionState
from tlon.app.clients.sdk.client import TlonClient
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
    - Automatic reconnection (delegated to the SDK's auto-reconnect, which
      re-authenticates and replays filters on every reconnect)
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

    # Reconnection configuration (enforced by the SDK's auto-reconnect)
    RECONNECT_DELAY = 1.0
    MAX_RECONNECT_ATTEMPTS = 10

    def __init__(
        self,
        ws_url: str = "ws://localhost:8888/ws",
        rest_url: str | None = None,
        auto_reconnect: bool = True,
        username: str | None = None,
    ):
        """Initialize Congress WebSocket client.

        Args:
            ws_url: WebSocket server URL
            rest_url: Optional REST API URL
            auto_reconnect: Whether to auto-reconnect on disconnect
            username: Human username to authenticate as (server default: guest)
        """
        self.ws_url = ws_url
        self.rest_url = rest_url
        self.auto_reconnect = auto_reconnect
        self.username = username

        # State management
        self.state = CongressState()
        self._connection_state = ClientConnectionState.DISCONNECTED
        # Mail ids already rendered (mail.delivered fires once per recipient)
        self._seen_mail_ids: set[str] = set()

        # SDK client
        self._adapter = TerminalAdapter()
        self._sdk: TlonClient | None = None

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
            auto_reconnect=self.auto_reconnect,
            reconnect_delay=self.RECONNECT_DELAY,
            max_reconnect_attempts=self.MAX_RECONNECT_ATTEMPTS,
        )

        # Set up SDK event handlers
        self._sdk.on_event(self._on_sdk_event)
        self._sdk.on_message(self._on_sdk_message)
        self._sdk.on_error(self._on_sdk_error)
        self._sdk.on_state_change(self._on_sdk_state_change)
        self._sdk.on_reconnect(self._on_reconnected)

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
            logger.debug("connect_skipped", reason="already_connected_or_connecting")
            return self.is_connected

        self._set_connection_state(ClientConnectionState.CONNECTING)

        try:
            if not self._sdk:
                self._init_sdk()

            # Connect
            connected = await self._sdk.connect()
            if not connected:
                logger.error("connect_failed")
                self._set_connection_state(ClientConnectionState.ERROR)
                return False

            # Authenticate
            self._set_connection_state(ClientConnectionState.AUTHENTICATING)
            authenticated = await self._sdk.authenticate(username=self.username)
            if not authenticated:
                logger.error("authentication_failed")
                self._set_connection_state(ClientConnectionState.ERROR)
                return False

            # Set mode to chat for Congress TUI
            await self._sdk.set_mode("chat")

            self._set_connection_state(ClientConnectionState.AUTHENTICATED)
            logger.info("connected_and_authenticated")

            # Load initial data
            await self._load_initial_data()

            return True

        except Exception as e:
            logger.error("connection_error", error=str(e), exc_info=True)
            self._set_connection_state(ClientConnectionState.ERROR)
            return False

    async def disconnect(self) -> None:
        """Disconnect from the server gracefully."""
        self._set_connection_state(ClientConnectionState.DISCONNECTING)

        # Disconnect SDK (cancels any pending auto-reconnect)
        if self._sdk:
            await self._sdk.disconnect()

        self._set_connection_state(ClientConnectionState.DISCONNECTED)
        logger.info("disconnected")

    def _set_connection_state(self, state: ClientConnectionState) -> None:
        """Set connection state and notify handlers."""
        old_state = self._connection_state
        self._connection_state = state
        self.state.connection_state = state.value

        if old_state != state:
            logger.debug(
                "connection_state_changed", old_state=old_state.value, new_state=state.value
            )
            for handler in self._connection_handlers:
                try:
                    if asyncio.iscoroutinefunction(handler):
                        asyncio.create_task(handler(state))
                    else:
                        handler(state)
                except Exception as e:
                    logger.error("connection_handler_error", error=str(e), exc_info=True)

    # ==================== Reconnection ====================

    def _on_sdk_state_change(self, state: ConnectionState) -> None:
        """Handle SDK state changes."""
        logger.debug("sdk_state_changed", state=state.value)

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

        # When an authenticated connection drops, the SDK auto-reconnect
        # engages immediately and reports RECONNECTING next — skip the
        # intermediate DISCONNECTED to avoid a UI status flicker.
        if (
            state == ConnectionState.DISCONNECTED
            and self.auto_reconnect
            and self._connection_state == ClientConnectionState.AUTHENTICATED
        ):
            logger.info("connection_lost", auto_reconnect=True)
            return

        self._set_connection_state(new_state)

    async def _on_reconnected(self) -> None:
        """Restore Congress session state after an SDK auto-reconnect.

        The SDK has already re-authenticated and replayed the declared
        filters; the server-side session is fresh, so re-declare the chat
        mode and reload agents/history.
        """
        if not self._sdk:
            return

        logger.info("session_restore_started")
        try:
            await self._sdk.set_mode("chat")
            await self._load_initial_data()
        except Exception as e:
            logger.warning("session_restore_failed", error=str(e), exc_info=True)

    # ==================== Message Handling ====================

    def _on_sdk_event(self, event: dict[str, Any]) -> None:
        """Handle SDK events."""
        event_type = event.get("type", "")
        payload = event.get("payload", {})

        logger.debug("event_received", event_type=event_type)

        # Handle different event types
        if event_type == "mail.delivered":
            self._handle_mail_delivered(payload)
        elif event_type == "agent.typing":
            self._handle_agent_typing(payload)
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
            logger.debug("pong_received")

    def _on_sdk_error(self, error_msg: str) -> None:
        """Handle SDK errors."""
        logger.error("sdk_error", error=error_msg)
        for handler in self._error_handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    asyncio.create_task(handler(error_msg))
                else:
                    handler(error_msg)
            except Exception as e:
                logger.error("error_handler_error", error=str(e), exc_info=True)

    def _handle_mail_delivered(self, payload: dict[str, Any]) -> None:
        """Handle a mail.delivered event (R1b minimal adaptation).

        The server pushes one event per recipient; the TUI renders each
        mail once, mapping the envelope (From/To/Subject/content) onto the
        existing message display structure. Recipient-aware views are R2.
        """
        mail_data = payload.get("mail", {})
        mail_id = mail_data.get("id")
        if mail_id and mail_id in self._seen_mail_ids:
            return
        if mail_id:
            self._seen_mail_ids.add(mail_id)

        from_address = mail_data.get("from_address", "")
        local = from_address.partition("@")[0]
        if local == "postmaster":
            sender_type = MessageType.SYSTEM
        elif local in self.state.agents:
            sender_type = MessageType.AGENT
        else:
            sender_type = MessageType.USER

        timestamp = mail_data.get("timestamp")
        if isinstance(timestamp, str):
            try:
                timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            except ValueError:
                timestamp = None

        message = Message(
            id=mail_id,
            content=mail_data.get("content", ""),
            sender_type=sender_type,
            sender_name=local or "Unknown",
            sender_id=from_address,
            timestamp=timestamp,
            mentions=mail_data.get("to", []),
            reply_to=mail_data.get("in_reply_to"),
            metadata={
                "to": mail_data.get("to", []),
                "cc": mail_data.get("cc", []),
                "subject": mail_data.get("subject"),
                "recipient": payload.get("recipient"),
            },
        )
        self.state.add_message(message)

        for handler in self._message_handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    asyncio.create_task(handler(message))
                else:
                    handler(message)
            except Exception as e:
                logger.error("message_handler_error", error=str(e), exc_info=True)

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
                logger.error("typing_handler_error", error=str(e), exc_info=True)

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
                logger.error("agent_handler_error", error=str(e), exc_info=True)

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
                logger.error("message_handler_error", error=str(e), exc_info=True)

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
            logger.warning("initial_data_load_failed", error=str(e), exc_info=True)

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
            logger.error("send_rejected", reason="not_connected")
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
            logger.error("message_send_failed", error=str(e), exc_info=True)
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
            logger.warning("ping_failed", error=str(e))
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
