"""Base Client for Tlon SDK.

Provides the core WebSocket connection management and protocol handling.
This is an internal class used by TlonClient, not intended for direct use.
"""

import asyncio
import contextlib
import json
import time
import uuid
from collections.abc import Callable
from enum import Enum
from typing import Any

import aiohttp

from tlon.base.logger import get_logger

from .exceptions import AuthenticationError, ConnectionError, ProtocolError

logger = get_logger(__name__)


class ConnectionState(Enum):
    """Connection state machine states."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    AUTHENTICATING = "authenticating"
    AUTHENTICATED = "authenticated"
    RECONNECTING = "reconnecting"
    ERROR = "error"


class BaseClient:
    """Base WebSocket client for Tlon Server.

    Handles low-level WebSocket operations:
    - Connection management (connect, disconnect, reconnect)
    - Protocol version negotiation
    - Authentication
    - Message sending/receiving
    - Heartbeat/ping-pong
    - Event dispatching

    This class is used internally by TlonClient. Users should use
    TlonClient instead of this class directly.

    Attributes:
        ws_url: WebSocket server URL
        rest_url: REST API URL
        state: Current connection state
        session_id: Server-assigned session ID (after auth)
        protocol_version: Negotiated protocol version

    Example:
        >>> client = BaseClient("ws://localhost:8888/ws")
        >>> await client.connect()
        >>> await client.authenticate("tui", capabilities=["trace", "filter"])
        >>> await client.send_message({"type": "message", "content": "Hello"})
    """

    # Protocol versions supported by this client
    SUPPORTED_VERSIONS = ["1.0.0"]
    PREFERRED_VERSION = "1.0.0"

    def __init__(
        self,
        ws_url: str,
        rest_url: str | None = None,
        auto_reconnect: bool = True,
        reconnect_delay: float = 5.0,
        max_reconnect_attempts: int = 10,
    ):
        """Initialize the base client.

        Args:
            ws_url: WebSocket server URL (e.g., "ws://localhost:8888/ws")
            rest_url: Optional REST API URL (defaults to ws_url with http:// and /ws removed)
            auto_reconnect: Whether to automatically reconnect on disconnect
            reconnect_delay: Delay between reconnection attempts in seconds
            max_reconnect_attempts: Maximum number of reconnection attempts (0 = unlimited)
        """
        self.ws_url = ws_url
        self.rest_url = rest_url or self._derive_rest_url(ws_url)
        self.auto_reconnect = auto_reconnect
        self.reconnect_delay = reconnect_delay
        self.max_reconnect_attempts = max_reconnect_attempts

        # Connection state
        self._state = ConnectionState.DISCONNECTED
        self._state_lock = asyncio.Lock()
        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None

        # Protocol state
        self.session_id: str | None = None
        self.protocol_version: str | None = None
        self.server_version: str | None = None
        self.server_capabilities: set[str] = set()

        # Event handling
        self._event_handlers: list[Callable[[dict[str, Any]], None]] = []
        self._message_handlers: list[Callable[[dict[str, Any]], None]] = []
        self._error_handlers: list[Callable[[str], None]] = []
        self._state_handlers: list[Callable[[ConnectionState], None]] = []
        self._reconnect_handlers: list[Callable[[], Any]] = []

        # Tasks
        self._receive_task: asyncio.Task | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._reconnect_task: asyncio.Task | None = None
        self._reconnect_count = 0

        # Event used to notify concurrent connect() callers
        self._connecting_event: asyncio.Event | None = None

        # Auth response coordination. The event is created by authenticate()
        # BEFORE the auth message is sent, so an early response is never lost.
        self._auth_response: dict[str, Any] | None = None
        self._auth_event: asyncio.Event | None = None

        # Params of the last authenticate() call, used by _reconnect() to
        # re-authenticate automatically after a reconnect.
        self._auth_params: dict[str, Any] | None = None

        # Message sequence for ping/pong
        self._ping_seq = 0
        self._pending_pongs: dict[int, asyncio.Future] = {}

    def _derive_rest_url(self, ws_url: str) -> str:
        """Derive REST URL from WebSocket URL.

        Args:
            ws_url: WebSocket URL

        Returns:
            Derived REST URL
        """
        # ws://localhost:8888/ws -> http://localhost:8888
        # wss://example.com/ws -> https://example.com
        rest = ws_url.replace("ws://", "http://").replace("wss://", "https://")
        if "/ws" in rest:
            rest = rest.replace("/ws", "")
        return rest

    # ==================== Properties ====================

    @property
    def state(self) -> ConnectionState:
        """Get current connection state."""
        return self._state

    @property
    def is_connected(self) -> bool:
        """Check if connected to server."""
        return self._state in (ConnectionState.CONNECTED, ConnectionState.AUTHENTICATED)

    @property
    def is_authenticated(self) -> bool:
        """Check if authenticated with server."""
        return self._state == ConnectionState.AUTHENTICATED

    # ==================== Connection Management ====================

    async def connect(self) -> bool:
        """Connect to the WebSocket server.

        Returns:
            True if connected successfully

        Raises:
            ConnectionError: If connection fails
        """
        async with self._state_lock:
            if self._state in (ConnectionState.CONNECTED, ConnectionState.AUTHENTICATED):
                return True
            if self._state == ConnectionState.CONNECTING:
                # Another coroutine is already connecting — wait for it to finish
                event = self._connecting_event
            else:
                # We are the one connecting — set up the event
                self._connecting_event = asyncio.Event()
                event = None
                self._state = ConnectionState.CONNECTING
                self._notify_state_change(ConnectionState.CONNECTING)

        if event is not None:
            await event.wait()
            return self._state in (ConnectionState.CONNECTED, ConnectionState.AUTHENTICATED)

        try:
            self._session = aiohttp.ClientSession()
            self._ws = await self._session.ws_connect(self.ws_url)

            async with self._state_lock:
                self._state = ConnectionState.CONNECTED
                self._notify_state_change(ConnectionState.CONNECTED)
                self._reconnect_count = 0
                if self._connecting_event:
                    self._connecting_event.set()
                    self._connecting_event = None

            # Start receive loop
            self._receive_task = asyncio.create_task(self._receive_loop())

            logger.info("connection_established", ws_url=self.ws_url)
            return True

        except Exception as e:
            logger.error("connection_failed", error=str(e), exc_info=True)
            async with self._state_lock:
                self._state = ConnectionState.ERROR
                self._notify_state_change(ConnectionState.ERROR)
                if self._connecting_event:
                    self._connecting_event.set()
                    self._connecting_event = None
            await self._cleanup()
            raise ConnectionError(f"Failed to connect: {e}") from e

    async def disconnect(self) -> None:
        """Disconnect from the server gracefully."""
        # Disable auto-reconnect
        self.auto_reconnect = False

        # Cancel reconnect if in progress
        if self._reconnect_task:
            self._reconnect_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reconnect_task
            self._reconnect_task = None

        await self._cleanup()

        async with self._state_lock:
            self._state = ConnectionState.DISCONNECTED
            self._notify_state_change(ConnectionState.DISCONNECTED)

        logger.info("disconnected")

    async def _cleanup(self) -> None:
        """Clean up resources."""
        # Stop heartbeat
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._heartbeat_task
            self._heartbeat_task = None

        # Stop receive loop
        if self._receive_task:
            self._receive_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._receive_task
            self._receive_task = None

        # Close WebSocket
        if self._ws:
            with contextlib.suppress(Exception):
                await self._ws.close()
            self._ws = None

        # Close session
        if self._session:
            with contextlib.suppress(Exception):
                await self._session.close()
            self._session = None

        # Clear pending pongs
        for future in self._pending_pongs.values():
            if not future.done():
                future.cancel()
        self._pending_pongs.clear()

        # Reset state
        self.session_id = None
        self.protocol_version = None
        self.server_version = None
        self.server_capabilities.clear()

    async def _receive_loop(self) -> None:
        """Main receive loop for WebSocket messages."""
        try:
            while self._ws and not self._ws.closed:
                msg = await self._ws.receive()

                if msg.type == aiohttp.WSMsgType.TEXT:
                    await self._handle_message(msg.data)
                elif msg.type == aiohttp.WSMsgType.CLOSED:
                    logger.info("websocket_closed")
                    break
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    logger.error("websocket_error", error=str(self._ws.exception()))
                    break

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("receive_loop_error", error=str(e), exc_info=True)
        finally:
            # Connection lost
            await self._on_connection_lost()

    async def _on_connection_lost(self) -> None:
        """Handle connection loss."""
        was_authenticated = self.is_authenticated

        async with self._state_lock:
            self._state = ConnectionState.DISCONNECTED
            self._notify_state_change(ConnectionState.DISCONNECTED)

        # _cleanup cancels _receive_task, but we are called from within it (finally block).
        # Clear the reference first so _cleanup skips cancelling the current task.
        self._receive_task = None
        await self._cleanup()

        # Trigger auto-reconnect if enabled
        if self.auto_reconnect and was_authenticated:
            self._reconnect_task = asyncio.create_task(self._reconnect())

    async def _reconnect(self) -> None:
        """Attempt to reconnect to the server."""
        while self.auto_reconnect:
            if (
                self.max_reconnect_attempts > 0
                and self._reconnect_count >= self.max_reconnect_attempts
            ):
                logger.error(
                    "max_reconnect_attempts_reached",
                    max_reconnect_attempts=self.max_reconnect_attempts,
                )
                async with self._state_lock:
                    self._state = ConnectionState.ERROR
                    self._notify_state_change(ConnectionState.ERROR)
                break

            self._reconnect_count += 1
            logger.info(
                "reconnect_scheduled",
                delay=self.reconnect_delay,
                attempt=self._reconnect_count,
            )

            async with self._state_lock:
                self._state = ConnectionState.RECONNECTING
                self._notify_state_change(ConnectionState.RECONNECTING)

            await asyncio.sleep(self.reconnect_delay)

            try:
                if await self.connect():
                    # Re-authenticate using the params from the original
                    # authenticate() call, otherwise the reconnected session
                    # is unusable (send() requires authentication).
                    if self._auth_params is not None:
                        try:
                            await self.authenticate(**self._auth_params)
                        except Exception as e:
                            logger.error("reauthentication_failed", error=str(e), exc_info=True)
                            await self._dispatch_error(f"Re-authentication failed: {e}")
                            continue
                    logger.info("reconnected")
                    self._notify_reconnect()
                    return
            except Exception as e:
                logger.warning("reconnect_attempt_failed", error=str(e), exc_info=True)

    # ==================== Authentication ====================

    async def authenticate(
        self,
        client_type: str,
        capabilities: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        timeout: float = 10.0,
        filters: dict[str, Any] | None = None,
    ) -> bool:
        """Authenticate with the server.

        Args:
            client_type: Type of client ("tui", "web", "telegram")
            capabilities: List of supported capabilities
            metadata: Optional client metadata
            timeout: Authentication timeout in seconds
            filters: Optional initial event filters declared by the client.
                The server enforces them for the whole session; later
                "filter" messages replace them wholesale.

        Returns:
            True if authenticated successfully

        Raises:
            ConnectionError: If not connected
            AuthenticationError: If authentication fails
            ProtocolError: If protocol version mismatch
        """
        if not self.is_connected:
            raise ConnectionError("Must connect before authenticating")

        # Idempotency: already authenticated
        if self.is_authenticated:
            return True

        async with self._state_lock:
            self._state = ConnectionState.AUTHENTICATING
            self._notify_state_change(ConnectionState.AUTHENTICATING)

        # Build auth message
        auth_msg = {
            "type": "auth",
            "client_type": client_type,
            "protocol_version": self.PREFERRED_VERSION,
            "supported_versions": self.SUPPORTED_VERSIONS,
            "capabilities": capabilities or [],
            "metadata": metadata or {},
        }
        if filters:
            auth_msg["filters"] = filters

        # TODO: Add msg_id and timestamp as per protocol spec
        # Waiting for Task 2.1 completion
        auth_msg["msg_id"] = str(uuid.uuid4())
        auth_msg["timestamp"] = time.time()

        # Remember auth params so _reconnect() can re-authenticate automatically
        self._auth_params = {
            "client_type": client_type,
            "capabilities": capabilities,
            "metadata": metadata,
            "filters": filters,
        }

        # Create the response event BEFORE sending the auth message, so a
        # response arriving before we start waiting is not dropped.
        self._auth_response = None
        self._auth_event = asyncio.Event()

        try:
            # Send auth message
            await self._send_raw(auth_msg)

            # Wait for auth response
            response = await asyncio.wait_for(self._wait_for_auth_response(), timeout=timeout)

            if response.get("type") == "auth_success":
                self.session_id = response.get("session_id")
                self.protocol_version = response.get("protocol_version")
                self.server_version = response.get("server_version")
                self.server_capabilities = set(response.get("capabilities", []))

                async with self._state_lock:
                    self._state = ConnectionState.AUTHENTICATED
                    self._notify_state_change(ConnectionState.AUTHENTICATED)

                # Start heartbeat
                self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

                logger.info(
                    "authenticated",
                    session_id=self.session_id,
                    protocol_version=self.protocol_version,
                )
                return True

            elif response.get("type") == "auth_failed":
                error_code = response.get("error_code", "UNKNOWN")
                message = response.get("message", "Authentication failed")

                if error_code == "VERSION_MISMATCH":
                    raise ProtocolError(
                        message=message, server_versions=response.get("supported_versions", [])
                    )
                else:
                    raise AuthenticationError(
                        message=message, supported_versions=response.get("supported_versions")
                    )
            else:
                raise AuthenticationError(f"Unexpected response: {response.get('type')}")

        except TimeoutError as e:
            raise AuthenticationError("Authentication timeout") from e

    async def _wait_for_auth_response(self) -> dict[str, Any]:
        """Wait for and return the auth response.

        This is called by authenticate() to wait for the server's response.
        The event is created by authenticate() before the auth message is
        sent, so a response that arrived earlier is still caught here.
        """
        event = self._auth_event
        if event is None:
            raise ProtocolError("authenticate() must create the auth event before waiting")
        await event.wait()
        return self._auth_response or {}

    # ==================== Message Handling ====================

    async def _handle_message(self, data: str) -> None:
        """Handle an incoming WebSocket message.

        Args:
            data: JSON message data
        """
        try:
            msg = json.loads(data)
            msg_type = msg.get("type")

            # Handle auth response
            if msg_type in ("auth_success", "auth_failed"):
                self._auth_response = msg
                if self._auth_event is not None:
                    self._auth_event.set()
                return

            # Handle pong
            if msg_type == "pong":
                seq = msg.get("seq")
                if seq in self._pending_pongs:
                    self._pending_pongs[seq].set_result(msg)
                    del self._pending_pongs[seq]
                return

            # Handle events
            if msg_type == "event":
                event = {
                    "type": msg.get("event_type"),
                    "payload": msg.get("payload"),
                    "source": msg.get("source"),
                    "timestamp": msg.get("timestamp"),
                    "trace_id": msg.get("trace_id"),
                    "span_id": msg.get("span_id"),
                    "parent_span_id": msg.get("parent_span_id"),
                }
                await self._dispatch_event(event)
                return

            # Handle errors
            if msg_type == "error":
                error_msg = msg.get("message", "Unknown error")
                await self._dispatch_error(error_msg)
                return

            # Pass to message handlers for other types
            await self._dispatch_message(msg)

        except json.JSONDecodeError:
            logger.error("invalid_json_received", data=data)
        except Exception as e:
            logger.error("message_handling_failed", error=str(e), exc_info=True)

    async def _dispatch_event(self, event: dict[str, Any]) -> None:
        """Dispatch event to registered handlers."""
        for handler in self._event_handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    asyncio.create_task(handler(event))
                else:
                    handler(event)
            except Exception as e:
                logger.error("event_handler_error", error=str(e), exc_info=True)

    async def _dispatch_message(self, msg: dict[str, Any]) -> None:
        """Dispatch message to registered handlers."""
        for handler in self._message_handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    asyncio.create_task(handler(msg))
                else:
                    handler(msg)
            except Exception as e:
                logger.error("message_handler_error", error=str(e), exc_info=True)

    async def _dispatch_error(self, error_msg: str) -> None:
        """Dispatch error to registered handlers."""
        for handler in self._error_handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    asyncio.create_task(handler(error_msg))
                else:
                    handler(error_msg)
            except Exception as e:
                logger.error("error_handler_error", error=str(e), exc_info=True)

    def _notify_state_change(self, state: ConnectionState) -> None:
        """Notify state change handlers."""
        for handler in self._state_handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    asyncio.create_task(handler(state))
                else:
                    handler(state)
            except Exception as e:
                logger.error("state_handler_error", error=str(e), exc_info=True)

    def _notify_reconnect(self) -> None:
        """Notify reconnect handlers after a successful auto-reconnect."""
        for handler in self._reconnect_handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    asyncio.create_task(handler())
                else:
                    handler()
            except Exception as e:
                logger.error("reconnect_handler_error", error=str(e), exc_info=True)

    # ==================== Sending Messages ====================

    async def send(self, data: dict[str, Any]) -> bool:
        """Send a message to the server.

        Args:
            data: Message data to send

        Returns:
            True if sent successfully
        """
        if not self.is_authenticated:
            logger.error("send_rejected", reason="not_authenticated")
            return False

        # Add msg_id and timestamp if not present
        if "msg_id" not in data:
            data["msg_id"] = str(uuid.uuid4())
        if "timestamp" not in data:
            data["timestamp"] = time.time()

        return await self._send_raw(data)

    async def _send_raw(self, data: dict[str, Any]) -> bool:
        """Send raw data to the server (internal use)."""
        if not self._ws or self._ws.closed:
            logger.error("send_rejected", reason="not_connected")
            return False

        try:
            await self._ws.send_str(json.dumps(data, ensure_ascii=False, default=str))
            return True
        except Exception as e:
            logger.error("message_send_failed", error=str(e), exc_info=True)
            return False

    # ==================== Heartbeat ====================

    async def _heartbeat_loop(self) -> None:
        """Send periodic ping messages."""
        try:
            while self.is_authenticated:
                await asyncio.sleep(30)  # 30 second interval

                if not self.is_authenticated:
                    break

                self._ping_seq += 1
                loop = asyncio.get_running_loop()
                ping_msg = {
                    "type": "ping",
                    "seq": self._ping_seq,
                    "timestamp": time.time(),
                }

                # Create future for pong response
                pong_future: asyncio.Future = loop.create_future()
                self._pending_pongs[self._ping_seq] = pong_future

                # Send ping
                if not await self._send_raw(ping_msg):
                    logger.warning("ping_send_failed")
                    continue

                # Wait for pong with timeout
                try:
                    await asyncio.wait_for(pong_future, timeout=5.0)
                    # TODO: Track latency
                except TimeoutError:
                    logger.warning("pong_timeout")
                    # Don't disconnect immediately, let next ping fail

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug("heartbeat_error", error=str(e))

    # ==================== Handler Registration ====================

    def on_event(self, handler: Callable[[dict[str, Any]], None]) -> Callable:
        """Register an event handler.

        Args:
            handler: Callback function for events

        Returns:
            The handler (for use as decorator)
        """
        self._event_handlers.append(handler)
        return handler

    def on_message(self, handler: Callable[[dict[str, Any]], None]) -> Callable:
        """Register a message handler.

        Args:
            handler: Callback function for messages

        Returns:
            The handler (for use as decorator)
        """
        self._message_handlers.append(handler)
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

    def on_state_change(self, handler: Callable[[ConnectionState], None]) -> Callable:
        """Register a state change handler.

        Args:
            handler: Callback function for state changes

        Returns:
            The handler (for use as decorator)
        """
        self._state_handlers.append(handler)
        return handler

    def on_reconnect(self, handler: Callable[[], Any]) -> Callable:
        """Register a reconnect handler.

        The handler is called after a successful auto-reconnect (including
        re-authentication), so clients can restore session state. It is not
        called for the initial connect.

        Args:
            handler: Callback function (sync or async) with no arguments

        Returns:
            The handler (for use as decorator)
        """
        self._reconnect_handlers.append(handler)
        return handler

    # ==================== REST API ====================

    async def rest_get(self, path: str, params: dict | None = None) -> dict | None:
        """Make a GET request to the REST API.

        Args:
            path: API path (e.g., "/agents")
            params: Optional query parameters

        Returns:
            Response JSON or None if failed
        """
        if not self._session:
            return None

        try:
            url = f"{self.rest_url}{path}"
            async with self._session.get(url, params=params) as resp:
                if resp.status == 200:
                    return await resp.json()
                else:
                    logger.warning("rest_get_failed", path=path, status=resp.status)
                    return None
        except Exception as e:
            logger.error("rest_get_error", path=path, error=str(e), exc_info=True)
            return None

    async def rest_post(self, path: str, data: dict) -> dict | None:
        """Make a POST request to the REST API.

        Args:
            path: API path
            data: Request body data

        Returns:
            Response JSON or None if failed
        """
        if not self._session:
            return None

        try:
            url = f"{self.rest_url}{path}"
            async with self._session.post(url, json=data) as resp:
                if resp.status in (200, 201):
                    return await resp.json()
                else:
                    logger.warning("rest_post_failed", path=path, status=resp.status)
                    return None
        except Exception as e:
            logger.error("rest_post_error", path=path, error=str(e), exc_info=True)
            return None
