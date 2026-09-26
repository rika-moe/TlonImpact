"""Base Client for Tlon SDK (protocol v2: REST + SSE).

Thin client over the HTTP+SSE protocol (docs/design/http-sse-protocol.md):

- Connect = create the HTTP session; a session is established with
  ``POST /sessions`` (returns session_id + participants/agents snapshot).
- Event subscription = ``GET /events`` SSE stream (``data:`` frames,
  ``: ping`` heartbeat comments ignored); on loss the client reconnects
  with exponential backoff — a reconnect re-POSTs ``/sessions`` for a
  fresh session_id and snapshot.
- ``set_mode`` / ``set_filters`` go through ``PATCH /sessions/{id}``;
  disconnect goes through ``DELETE /sessions/{id}`` + aborting the stream.

This is an internal class used by TlonClient, not intended for direct use.
"""

import asyncio
import contextlib
import json
from collections.abc import Callable, Sequence
from enum import Enum
from typing import Any, cast

import aiohttp
import structlog

from .exceptions import AuthenticationError, ConnectionError, ProtocolError

logger = structlog.get_logger(__name__)


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
    """Base REST + SSE client for Tlon Server (protocol v2).

    Handles low-level transport operations:
    - Session lifecycle (POST/PATCH/DELETE /sessions)
    - SSE event stream with automatic reconnect (exponential backoff)
    - Event dispatching

    This class is used internally by TlonClient. Users should use
    TlonClient instead of this class directly.

    Attributes:
        base_url: REST API base URL (single port, e.g. "http://localhost:8080")
        state: Current connection state
        session_id: Server-assigned session ID (after authenticate)
        protocol_version: Protocol version in use ("2.0.0")
        participants: Member snapshot from the session response
        agents: Agent snapshot from the session response

    Example:
        >>> client = BaseClient("http://localhost:8080")
        >>> await client.connect()
        >>> await client.authenticate("tui", username="alice")
        >>> await client.rest_post("/messages", {"content": "Hello", "to": ["dev-001"]})
    """

    # The only protocol version POST /sessions accepts.
    PROTOCOL_VERSION = "2.0.0"

    # Upper bound for the exponential reconnect backoff (seconds).
    MAX_RECONNECT_DELAY = 60.0

    def __init__(
        self,
        base_url: str = "http://localhost:8080",
        auto_reconnect: bool = True,
        reconnect_delay: float = 5.0,
        max_reconnect_attempts: int = 10,
    ):
        """Initialize the base client.

        Args:
            base_url: REST API base URL (e.g., "http://localhost:8080")
            auto_reconnect: Whether to automatically reconnect on stream loss
            reconnect_delay: Base delay between reconnection attempts in
                seconds (grows exponentially up to MAX_RECONNECT_DELAY)
            max_reconnect_attempts: Maximum number of reconnection attempts (0 = unlimited)
        """
        self.base_url = base_url.rstrip("/")
        self.auto_reconnect = auto_reconnect
        self.reconnect_delay = reconnect_delay
        self.max_reconnect_attempts = max_reconnect_attempts

        # Connection state
        self._state = ConnectionState.DISCONNECTED
        self._state_lock = asyncio.Lock()
        self._http: aiohttp.ClientSession | None = None
        self._closing = False

        # Protocol state (populated from the POST /sessions response)
        self.session_id: str | None = None
        self.protocol_version: str | None = None
        self.username: str | None = None
        self.mode: str = "chat"
        self.filters: dict[str, Any] = {}
        self.participants: list[dict[str, Any]] = []
        self.agents: list[dict[str, Any]] = []

        # Event handling
        self._event_handlers: list[Callable[[dict[str, Any]], None]] = []
        self._message_handlers: list[Callable[[dict[str, Any]], None]] = []
        self._error_handlers: list[Callable[[str], None]] = []
        self._state_handlers: list[Callable[[ConnectionState], None]] = []
        self._reconnect_handlers: list[Callable[[], Any]] = []

        # Tasks
        self._sse_task: asyncio.Task[None] | None = None
        self._reconnect_task: asyncio.Task[None] | None = None
        self._reconnect_count = 0
        # Strong references to in-flight handler tasks (asyncio only holds
        # weak references, so untracked tasks could be GC'd mid-run)
        self._handler_tasks: set[asyncio.Task[None]] = set()

        # Params of the last authenticate() call, used by _reconnect() to
        # re-establish the session automatically after a reconnect.
        self._auth_params: dict[str, Any] | None = None

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
        """Check if a session is established with the server."""
        return self._state == ConnectionState.AUTHENTICATED

    # ==================== Connection Management ====================

    async def connect(self) -> bool:
        """Connect to the server (create the HTTP session).

        No session is established yet — that happens in authenticate()
        via POST /sessions.

        Returns:
            True if connected successfully

        Raises:
            ConnectionError: If the HTTP session cannot be created
        """
        async with self._state_lock:
            if self._state in (ConnectionState.CONNECTED, ConnectionState.AUTHENTICATED):
                return True
            self._state = ConnectionState.CONNECTING
            self._notify_state_change(ConnectionState.CONNECTING)

        try:
            self._closing = False
            self._http = aiohttp.ClientSession()

            async with self._state_lock:
                self._state = ConnectionState.CONNECTED
                self._notify_state_change(ConnectionState.CONNECTED)
                self._reconnect_count = 0

            logger.info("connection_established", base_url=self.base_url)
            return True

        except Exception as e:
            logger.error("connection_failed", error=str(e), exc_info=True)
            async with self._state_lock:
                self._state = ConnectionState.ERROR
                self._notify_state_change(ConnectionState.ERROR)
            await self._cleanup()
            raise ConnectionError(f"Failed to connect: {e}") from e

    async def disconnect(self) -> None:
        """Disconnect from the server gracefully.

        Aborts the SSE stream and deletes the server-side session
        (DELETE /sessions/{id}, best-effort).
        """
        # Disable auto-reconnect and mark intentional teardown
        self.auto_reconnect = False
        self._closing = True

        # Cancel reconnect if in progress
        if self._reconnect_task:
            self._reconnect_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reconnect_task
            self._reconnect_task = None

        # Tell the server the session is gone (best-effort, idempotent)
        if self._http and self.session_id:
            try:
                async with self._http.delete(
                    f"{self.base_url}/sessions/{self.session_id}",
                    timeout=aiohttp.ClientTimeout(total=5.0),
                ):
                    pass
            except Exception as e:
                logger.debug("session_delete_failed", error=str(e))

        await self._cleanup()

        async with self._state_lock:
            self._state = ConnectionState.DISCONNECTED
            self._notify_state_change(ConnectionState.DISCONNECTED)

        logger.info("disconnected")

    async def _cleanup(self) -> None:
        """Clean up resources."""
        # Cancel pending handler tasks (done callbacks may mutate the set,
        # so iterate over a snapshot)
        pending_handlers = list(self._handler_tasks)
        for task in pending_handlers:
            task.cancel()
        for task in pending_handlers:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._handler_tasks.clear()

        # Stop the SSE stream
        if self._sse_task:
            self._sse_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._sse_task
            self._sse_task = None

        # Close the HTTP session
        if self._http:
            with contextlib.suppress(Exception):
                await self._http.close()
            self._http = None

        # Reset protocol state
        self.session_id = None
        self.protocol_version = None
        self.username = None
        self.mode = "chat"
        self.filters = {}
        self.participants = []
        self.agents = []

    async def _on_connection_lost(self) -> None:
        """Handle SSE stream loss."""
        if self._closing:
            return
        was_authenticated = self.is_authenticated

        async with self._state_lock:
            self._state = ConnectionState.DISCONNECTED
            self._notify_state_change(ConnectionState.DISCONNECTED)

        # _cleanup cancels _sse_task, but we are called from within it
        # (finally block). Clear the reference first so _cleanup skips
        # cancelling the current task.
        self._sse_task = None
        await self._cleanup()

        # Trigger auto-reconnect if enabled
        if self.auto_reconnect and was_authenticated and not self._closing:
            self._reconnect_task = asyncio.create_task(self._reconnect())

    async def _reconnect(self) -> None:
        """Reconnect with exponential backoff: re-POST /sessions + reopen SSE."""
        while self.auto_reconnect and not self._closing:
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
            delay = min(
                self.reconnect_delay * (2 ** (self._reconnect_count - 1)),
                self.MAX_RECONNECT_DELAY,
            )
            logger.info(
                "reconnect_scheduled",
                delay=delay,
                attempt=self._reconnect_count,
            )

            async with self._state_lock:
                self._state = ConnectionState.RECONNECTING
                self._notify_state_change(ConnectionState.RECONNECTING)

            await asyncio.sleep(delay)

            if self._closing:
                break  # type: ignore[unreachable]  # 属性由事件循环/其他协程变更
            try:
                # _on_connection_lost closed the HTTP session; recreate it
                if self._http is None:
                    self._http = aiohttp.ClientSession()
                await self._open_session(**(self._auth_params or {}))
            except Exception as e:
                logger.warning("reconnect_attempt_failed", error=str(e))
                continue

            async with self._state_lock:
                self._state = ConnectionState.AUTHENTICATED
                self._notify_state_change(ConnectionState.AUTHENTICATED)
                self._reconnect_count = 0
            self._sse_task = asyncio.create_task(self._sse_loop())

            logger.info("reconnected", session_id=self.session_id)
            self._notify_reconnect()
            return

    # ==================== Session Establishment ====================

    async def authenticate(
        self,
        client_type: str,
        capabilities: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        timeout: float = 10.0,
        filters: dict[str, Any] | None = None,
        username: str | None = None,
        mode: str = "chat",
    ) -> bool:
        """Establish a session with the server (POST /sessions).

        Maps to the v2 session request: ``username`` / ``client_type`` /
        ``mode`` / ``filters`` / ``protocol_version``. ``capabilities`` and
        ``metadata`` are accepted for API compatibility but are not part of
        the v2 session contract and are not sent.

        Args:
            client_type: Type of client ("tui", "web", "telegram")
            capabilities: Unused in protocol v2 (kept for compatibility)
            metadata: Unused in protocol v2 (kept for compatibility)
            timeout: Session establishment timeout in seconds
            filters: Optional initial event filters declared by the client.
                The server enforces them for the whole session; later
                update_session() calls replace them wholesale.
            username: Optional human username — determines the
                "human:<username>" identity this session speaks as
                (server defaults to "guest" when omitted)
            mode: Initial mode ("chat" | "event" | "agent")

        Returns:
            True if the session was established successfully

        Raises:
            ConnectionError: If not connected
            AuthenticationError: If the session request fails
            ProtocolError: If the protocol version is rejected
        """
        if not self.is_connected:
            raise ConnectionError("Must connect before authenticating")

        # Idempotency: already authenticated
        if self.is_authenticated:
            return True

        async with self._state_lock:
            self._state = ConnectionState.AUTHENTICATING
            self._notify_state_change(ConnectionState.AUTHENTICATING)

        # Remember params so _reconnect() can re-establish the session
        self._auth_params = {
            "client_type": client_type,
            "timeout": timeout,
            "filters": filters,
            "username": username,
            "mode": mode,
        }

        try:
            await self._open_session(
                client_type=client_type,
                timeout=timeout,
                filters=filters,
                username=username,
                mode=mode,
            )
        except Exception:
            async with self._state_lock:
                self._state = ConnectionState.CONNECTED
                self._notify_state_change(ConnectionState.CONNECTED)
            raise

        async with self._state_lock:
            self._state = ConnectionState.AUTHENTICATED
            self._notify_state_change(ConnectionState.AUTHENTICATED)
            self._reconnect_count = 0

        # Open the SSE event stream
        self._sse_task = asyncio.create_task(self._sse_loop())

        logger.info(
            "authenticated",
            session_id=self.session_id,
            protocol_version=self.protocol_version,
        )
        return True

    async def _open_session(
        self,
        client_type: str,
        timeout: float = 10.0,
        filters: dict[str, Any] | None = None,
        username: str | None = None,
        mode: str = "chat",
    ) -> None:
        """POST /sessions and store the returned session state + snapshot."""
        if not self._http:
            raise ConnectionError("Must connect before authenticating")

        body: dict[str, Any] = {
            "client_type": client_type,
            "mode": mode,
            "protocol_version": self.PROTOCOL_VERSION,
        }
        if username is not None:
            body["username"] = username
        if filters:
            body["filters"] = filters

        try:
            async with self._http.post(
                f"{self.base_url}/sessions",
                json=body,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                data = cast(dict[str, Any], await resp.json())
                if resp.status == 201:
                    self.session_id = cast(str, data["session_id"])
                    self.protocol_version = self.PROTOCOL_VERSION
                    self.username = cast(str | None, data.get("username"))
                    self.mode = cast(str, data.get("mode", mode))
                    self.filters = cast(dict[str, Any], data.get("filters") or {})
                    self.participants = cast(list[dict[str, Any]], data.get("participants") or [])
                    self.agents = cast(list[dict[str, Any]], data.get("agents") or [])
                    return

                reason = data.get("reason", "")
                message = data.get("error", "Session request failed")
                if reason == "unsupported_protocol_version":
                    raise ProtocolError(
                        message=message,
                        server_versions=data.get("supported_versions", []),
                    )
                raise AuthenticationError(message)
        except TimeoutError as e:
            raise AuthenticationError("Authentication timeout") from e

    # ==================== Session Updates ====================

    async def update_session(
        self,
        mode: str | None = None,
        filters: dict[str, Any] | None = None,
    ) -> bool:
        """Update the session's mode and/or filters (PATCH /sessions/{id}).

        The response body carries the latest session state (no mode_changed
        frame is pushed, unlike the retired WS protocol); local mode/filters
        are synced from it.

        Args:
            mode: New mode ("chat" | "event" | "agent"), optional
            filters: New filters object (replaces the session's filters), optional

        Returns:
            True if the server accepted the update
        """
        if not self.is_authenticated or not self._http or not self.session_id:
            logger.error("session_update_rejected", reason="not_authenticated")
            return False

        body: dict[str, Any] = {}
        if mode is not None:
            body["mode"] = mode
        if filters is not None:
            body["filters"] = filters

        try:
            async with self._http.patch(
                f"{self.base_url}/sessions/{self.session_id}", json=body
            ) as resp:
                if resp.status != 200:
                    logger.warning("session_update_failed", status=resp.status)
                    return False
                data = cast(dict[str, Any], await resp.json())
                self.mode = cast(str, data.get("mode", self.mode))
                self.filters = cast(dict[str, Any], data.get("filters") or {})
                return True
        except Exception as e:
            logger.error("session_update_error", error=str(e), exc_info=True)
            return False

    # ==================== SSE Event Stream ====================

    async def _sse_loop(self) -> None:
        """Run the SSE stream; on exit, handle the connection loss."""
        try:
            await self._stream_events()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("sse_loop_error", error=str(e), exc_info=True)
        finally:
            await self._on_connection_lost()

    async def _stream_events(self) -> None:
        """Read and dispatch SSE frames from GET /events until the stream ends."""
        if not self._http or not self.session_id:
            return

        # No total timeout: the stream is long-lived (server heartbeats
        # every 25s keep it alive).
        async with self._http.get(
            f"{self.base_url}/events",
            params={"session_id": self.session_id},
            timeout=aiohttp.ClientTimeout(total=None),
        ) as resp:
            if resp.status != 200:
                raise ConnectionError(f"SSE stream rejected: status {resp.status}")

            data_lines: list[str] = []
            async for raw_line in resp.content:
                line = raw_line.decode("utf-8").rstrip("\r\n")
                if not line:
                    # Blank line: end of frame — dispatch accumulated data
                    if data_lines:
                        await self._handle_frame("\n".join(data_lines))
                        data_lines = []
                elif line.startswith(":"):
                    # Heartbeat comment (": ping") — ignore
                    continue
                elif line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())

    async def _handle_frame(self, data: str) -> None:
        """Handle one SSE data payload (a JSON event frame).

        Args:
            data: JSON frame data
        """
        try:
            msg = json.loads(data)
            msg_type = msg.get("type")

            # Handle events: map the wire frame to the dispatch shape
            # (same mapping the retired WS client used)
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
                self._dispatch_event(event)
                return

            # Handle errors
            if msg_type == "error":
                error_msg = msg.get("message", "Unknown error")
                self._dispatch_error(error_msg)
                return

            # Pass to message handlers for other types
            self._dispatch_message(msg)

        except json.JSONDecodeError:
            logger.error("invalid_json_received", data=data)
        except Exception as e:
            logger.error("frame_handling_failed", error=str(e), exc_info=True)

    def _fire(
        self, handlers: Sequence[Callable[..., Any]], log_event: str, *args: Any
    ) -> None:
        """Dispatch to registered handlers (sync inline, coroutine as a task)."""
        for handler in handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    self._spawn_handler_task(handler, log_event, *args)
                else:
                    handler(*args)
            except Exception as e:
                logger.error(log_event, error=str(e), exc_info=True)

    def _spawn_handler_task(
        self, handler: Callable[..., Any], log_event: str, *args: Any
    ) -> None:
        """Run a coroutine handler as a tracked task; failures are logged."""
        task = asyncio.create_task(handler(*args))
        self._handler_tasks.add(task)

        def _on_done(completed: asyncio.Task[None]) -> None:
            self._handler_tasks.discard(completed)
            # task.exception() raises CancelledError for cancelled tasks
            if completed.cancelled():
                return
            exc = completed.exception()
            if exc is not None:
                logger.error(log_event, error=str(exc), exc_info=exc)

        task.add_done_callback(_on_done)

    def _dispatch_event(self, event: dict[str, Any]) -> None:
        """Dispatch event to registered handlers."""
        self._fire(self._event_handlers, "event_handler_error", event)

    def _dispatch_message(self, msg: dict[str, Any]) -> None:
        """Dispatch message to registered handlers."""
        self._fire(self._message_handlers, "message_handler_error", msg)

    def _dispatch_error(self, error_msg: str) -> None:
        """Dispatch error to registered handlers."""
        self._fire(self._error_handlers, "error_handler_error", error_msg)

    def _notify_state_change(self, state: ConnectionState) -> None:
        """Notify state change handlers."""
        self._fire(self._state_handlers, "state_handler_error", state)

    def _notify_reconnect(self) -> None:
        """Notify reconnect handlers after a successful auto-reconnect."""
        self._fire(self._reconnect_handlers, "reconnect_handler_error")

    # ==================== Handler Registration ====================

    def on_event(self, handler: Callable[[dict[str, Any]], None]) -> Callable[[dict[str, Any]], None]:
        """Register an event handler.

        Args:
            handler: Callback function for events

        Returns:
            The handler (for use as decorator)
        """
        self._event_handlers.append(handler)
        return handler

    def on_message(self, handler: Callable[[dict[str, Any]], None]) -> Callable[[dict[str, Any]], None]:
        """Register a message handler.

        Args:
            handler: Callback function for messages

        Returns:
            The handler (for use as decorator)
        """
        self._message_handlers.append(handler)
        return handler

    def on_error(self, handler: Callable[[str], None]) -> Callable[[str], None]:
        """Register an error handler.

        Args:
            handler: Callback function for errors

        Returns:
            The handler (for use as decorator)
        """
        self._error_handlers.append(handler)
        return handler

    def on_state_change(self, handler: Callable[[ConnectionState], None]) -> Callable[[ConnectionState], None]:
        """Register a state change handler.

        Args:
            handler: Callback function for state changes

        Returns:
            The handler (for use as decorator)
        """
        self._state_handlers.append(handler)
        return handler

    def on_reconnect(self, handler: Callable[[], Any]) -> Callable[[], Any]:
        """Register a reconnect handler.

        The handler is called after a successful auto-reconnect (the
        session has been re-established via POST /sessions and the SSE
        stream reopened), so clients can restore session state. It is not
        called for the initial connect.

        Args:
            handler: Callback function (sync or async) with no arguments

        Returns:
            The handler (for use as decorator)
        """
        self._reconnect_handlers.append(handler)
        return handler

    # ==================== REST API ====================

    async def rest_get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Make a GET request to the REST API.

        Args:
            path: API path (e.g., "/agents")
            params: Optional query parameters

        Returns:
            Response JSON or None if failed
        """
        if not self._http:
            return None

        try:
            url = f"{self.base_url}{path}"
            async with self._http.get(url, params=params) as resp:
                if resp.status == 200:
                    return cast(dict[Any, Any], await resp.json())
                else:
                    logger.warning("rest_get_failed", path=path, status=resp.status)
                    return None
        except Exception as e:
            logger.error("rest_get_error", path=path, error=str(e), exc_info=True)
            return None

    async def rest_post(self, path: str, data: dict[str, Any]) -> dict[str, Any] | None:
        """Make a POST request to the REST API.

        Args:
            path: API path
            data: Request body data

        Returns:
            Response JSON or None if failed
        """
        if not self._http:
            return None

        try:
            url = f"{self.base_url}{path}"
            async with self._http.post(url, json=data) as resp:
                if resp.status in (200, 201, 202):
                    return cast(dict[Any, Any], await resp.json())
                else:
                    logger.warning("rest_post_failed", path=path, status=resp.status)
                    return None
        except Exception as e:
            logger.error("rest_post_error", path=path, error=str(e), exc_info=True)
            return None
