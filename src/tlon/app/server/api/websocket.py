"""WebSocket API for Tlon Server.

Provides real-time bidirectional communication:
- Server → Client: Events (messages, agent state changes)
- Client → Server: Commands (send message, change mode, etc.)

Protocol:
All messages are JSON with a "type" field:
- Client → Server:
  - {"type": "auth", "client_type": "tui", "username": "alice", "filters": {...}, ...}
  - {"type": "message", "content": "...", "mentions": [...]}
  - {"type": "mode", "mode": "chat|event|agent"}
  - {"type": "filter", "filters": {...}}
  - {"type": "ping"}

  The auth "username" is optional and defaults to "guest"; it determines
  the human identity ("human:<username>") the session speaks as.

  Filter dimensions (declared by the client, enforced by the server):
  - event_types: include list ("prefix.*" wildcard supported)
  - exclude_event_types: exclusion list ("prefix.*" wildcard supported)
  - trace_id: only events carrying this trace ID
  - agent: event source pattern ("*" wildcard supported)

- Server → Client:
  - {"type": "event", "event_type": "...", "payload": {...}}
  - {"type": "auth_success", "session_id": "...", "username": "..."}
  - {"type": "error", "message": "..."}
  - {"type": "pong"}
"""

import asyncio
import json
from datetime import datetime

from aiohttp import WSMsgType, web

from tlon.base.events.event import Event
from tlon.base.logger import get_logger
from tlon.organization.messaging.human_adapter import DEFAULT_USERNAME, AdapterManager

logger = get_logger(__name__)


class WebSocketHandler:
    """Handler for WebSocket connections.

    Manages client connections and handles bidirectional communication.

    Example:
        >>> ws_handler = WebSocketHandler(client_gateway, agent_registry, adapter_manager)
        >>> app = web.Application()
        >>> app.router.add_get('/ws', ws_handler.handle_websocket)
    """

    def __init__(self, client_gateway, agent_registry, adapter_manager: AdapterManager):
        """Initialize the WebSocket handler.

        Args:
            client_gateway: The ClientGateway instance
            agent_registry: The AgentRegistry instance for agent operations
            adapter_manager: The AdapterManager for human identity and speaking
        """
        self.client_gateway = client_gateway
        self.agent_registry = agent_registry
        self.adapter_manager = adapter_manager
        self._connections: dict[str, web.WebSocketResponse] = {}
        self._lock = asyncio.Lock()

    async def handle_websocket(self, request: web.Request) -> web.WebSocketResponse:
        """Handle a new WebSocket connection.

        This is the main entry point for WebSocket connections.

        Args:
            request: The HTTP request that was upgraded to WebSocket

        Returns:
            WebSocketResponse (connection remains open until closed)
        """
        ws = web.WebSocketResponse(
            heartbeat=30.0,  # Send ping every 30 seconds
            autoping=True,
        )
        await ws.prepare(request)

        session_id: str | None = None

        try:
            # Wait for authentication message
            auth_result = await self._authenticate(ws)
            if not auth_result:
                await ws.close(code=4001, message=b"Authentication failed")
                return ws

            session, session_id = auth_result

            # Store connection
            async with self._lock:
                self._connections[session_id] = ws

            logger.info("websocket_connected", session_id=session_id)

            # Send initial data based on mode
            await self._send_initial_data(ws, session)

            # Handle incoming messages
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    await self._handle_client_message(session_id, msg.data)
                elif msg.type == WSMsgType.ERROR:
                    logger.error(
                        "websocket_error", session_id=session_id, error=str(ws.exception())
                    )
                elif msg.type == WSMsgType.CLOSE:
                    break

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("websocket_error", session_id=session_id, error=str(e), exc_info=True)
        finally:
            # Cleanup
            if session_id:
                async with self._lock:
                    self._connections.pop(session_id, None)
                await self.adapter_manager.detach(session_id)
                await self.client_gateway.disconnect_client(session_id)
                logger.info("websocket_disconnected", session_id=session_id)

        return ws

    # Protocol version constants
    PROTOCOL_VERSION = "1.0.0"
    SUPPORTED_VERSIONS = ["1.0.0"]
    MIN_SUPPORTED_VERSION = "1.0.0"

    async def _authenticate(self, ws: web.WebSocketResponse) -> tuple | None:
        """Authenticate a new WebSocket connection with protocol version negotiation.

        Waits for an auth message from the client and negotiates protocol version.

        Args:
            ws: The WebSocket connection

        Returns:
            Tuple of (session, session_id) or None if auth failed
        """
        try:
            # Wait for auth message with timeout
            msg = await asyncio.wait_for(ws.receive(), timeout=10.0)

            if msg.type != WSMsgType.TEXT:
                await self._send_error(ws, "Expected text message for authentication")
                return None

            data = json.loads(msg.data)

            if data.get("type") != "auth":
                await self._send_error(ws, "Expected auth message")
                return None

            client_type = data.get("client_type", "unknown")
            metadata = data.get("metadata", {})

            # Human identity: optional username, defaults to "guest"
            username_raw = data.get("username")
            username = (
                username_raw.strip()
                if isinstance(username_raw, str) and username_raw.strip()
                else DEFAULT_USERNAME
            )

            # Protocol version negotiation
            client_protocol_version = data.get("protocol_version")
            client_supported_versions = data.get("supported_versions", [])
            capabilities = data.get("capabilities", [])

            # Negotiate protocol version
            negotiated_version, error = self._negotiate_protocol_version(
                client_protocol_version, client_supported_versions
            )

            if error:
                await self._send_message(
                    ws,
                    {
                        "type": "auth_failed",
                        "error_code": "VERSION_MISMATCH",
                        "message": error,
                        "supported_versions": self.SUPPORTED_VERSIONS,
                    },
                )
                return None

            # Define event handler for this session
            async def event_handler(event: Event) -> None:
                await self._send_event(ws, event)

            # Connect client to server. Initial filters declared by the client
            # (e.g. client-type default exclusions) become the session filters;
            # all event filtering is then enforced server-side.
            filters = data.get("filters")
            if not isinstance(filters, dict):
                filters = None

            session = await self.client_gateway.connect_client(
                client_type=client_type,
                event_handler=event_handler,
                metadata={
                    **metadata,
                    "protocol_version": negotiated_version,
                    "capabilities": capabilities,
                    "username": username,
                },
                filters=filters,
            )

            # Attach the session to the user's human adapter (creates it on
            # first attach; shared across same-name sessions)
            await self.adapter_manager.attach(username, session.id)

            # Send auth success with negotiated version
            await self._send_message(
                ws,
                {
                    "type": "auth_success",
                    "session_id": session.id,
                    "username": username,
                    "protocol_version": negotiated_version,
                    "server_version": "0.2.0",
                    "server_time": datetime.now().isoformat(),
                    "capabilities": ["trace", "filter", "history"],
                    "timestamp": session.connected_at,
                },
            )

            logger.info(
                "client_authenticated",
                session_id=session.id,
                client_type=client_type,
                username=username,
                protocol_version=negotiated_version,
            )
            return session, session.id

        except TimeoutError:
            await self._send_error(ws, "Authentication timeout")
            return None
        except json.JSONDecodeError:
            await self._send_error(ws, "Invalid JSON")
            return None
        except Exception as e:
            logger.error("authentication_error", error=str(e), exc_info=True)
            await self._send_error(ws, f"Authentication failed: {str(e)}")
            return None

    def _negotiate_protocol_version(
        self, client_preferred: str | None, client_supported: list[str]
    ) -> tuple:
        """Negotiate protocol version between client and server.

        Args:
            client_preferred: Client's preferred protocol version
            client_supported: List of versions client supports

        Returns:
            Tuple of (negotiated_version, error_message)
            If successful, error_message is None
            If failed, negotiated_version is None
        """
        # Backward compatibility: if no version specified, assume 1.0.0
        if not client_preferred and not client_supported:
            return ("1.0.0", None)

        # If client only provides preferred version
        if client_preferred and not client_supported:
            client_supported = [client_preferred]

        # If client only provides supported versions (no preferred)
        if client_supported and not client_preferred:
            client_preferred = client_supported[0]

        # Check if client's preferred version is supported
        if client_preferred in self.SUPPORTED_VERSIONS:
            return (client_preferred, None)

        # Find highest common version
        common_versions = set(client_supported) & set(self.SUPPORTED_VERSIONS)
        if common_versions:
            # Return highest common version (simple string comparison works for semver)
            highest = max(common_versions)
            logger.debug(
                "protocol_version_negotiated",
                protocol_version=highest,
                supported_versions=client_supported,
            )
            return (highest, None)

        # Check if client version is too old
        if client_preferred < self.MIN_SUPPORTED_VERSION:
            return (
                None,
                f"Protocol version {client_preferred} is no longer supported. Please upgrade your client.",
            )

        # Check if client version is newer than server
        if client_preferred > self.PROTOCOL_VERSION:
            return (self.PROTOCOL_VERSION, None)

        # Version mismatch
        return (None, f"Protocol version {client_preferred} not supported")

    async def _handle_client_message(self, session_id: str, data: str) -> None:
        """Handle a message from a client.

        Args:
            session_id: The client's session ID
            data: The message data (JSON string)
        """
        try:
            msg = json.loads(data)
            msg_type = msg.get("type")

            if msg_type == "ping":
                await self._send_pong(session_id)

            elif msg_type == "message":
                await self._handle_send_message(session_id, msg)

            elif msg_type == "mode":
                await self._handle_mode_change(session_id, msg)

            elif msg_type == "filter":
                await self._handle_filter_change(session_id, msg)

            else:
                await self._send_error_to_session(session_id, f"Unknown message type: {msg_type}")

        except json.JSONDecodeError:
            await self._send_error_to_session(session_id, "Invalid JSON")
        except Exception as e:
            logger.error("client_message_error", session_id=session_id, error=str(e))
            await self._send_error_to_session(session_id, f"Error: {str(e)}")

    async def _handle_send_message(self, session_id: str, msg: dict) -> None:
        """Handle a send message request from client."""
        content = msg.get("content", "").strip()
        mentions = msg.get("mentions", [])
        username = self.adapter_manager.username_for(session_id)
        logger.info(
            "message_received",
            session_id=session_id,
            username=username,
            message_id=msg.get("id"),
            content=content,
            mentions=mentions,
        )

        if not content:
            await self._send_error_to_session(session_id, "Message content is empty")
            return

        # Speak through the user's human adapter (R1b bridge: @mentions in
        # the content are resolved to recipient addresses; a mail with zero
        # recipients is rejected) and publishes MAIL_NEW as "human:<username>"
        try:
            mail = await self.adapter_manager.publish(session_id, content, mentions)
        except ValueError as e:
            await self._send_error_to_session(session_id, str(e))
            return
        if mail is None:
            await self._send_error_to_session(
                session_id, "No human adapter attached to this session"
            )

    async def _handle_mode_change(self, session_id: str, msg: dict) -> None:
        """Handle a mode change request from client."""
        mode = msg.get("mode")
        if mode not in ("chat", "event", "agent"):
            await self._send_error_to_session(session_id, f"Invalid mode: {mode}")
            return

        success = await self.client_gateway.update_session_mode(session_id, mode)
        if success:
            await self._send_message_to_session(
                session_id,
                {
                    "type": "mode_changed",
                    "mode": mode,
                },
            )
            # Send initial data for new mode
            session = self.client_gateway.get_session(session_id)
            if session:
                ws = self._connections.get(session_id)
                if ws:
                    await self._send_initial_data(ws, session)
        else:
            await self._send_error_to_session(session_id, "Failed to change mode")

    async def _handle_filter_change(self, session_id: str, msg: dict) -> None:
        """Handle a filter change request from client."""
        filters = msg.get("filters", {})

        success = await self.client_gateway.update_session_filters(session_id, filters)
        if success:
            await self._send_message_to_session(
                session_id,
                {
                    "type": "filters_updated",
                    "filters": filters,
                },
            )
        else:
            await self._send_error_to_session(session_id, "Failed to update filters")

    async def _send_initial_data(self, ws: web.WebSocketResponse, session) -> None:
        """Send initial data to a client based on their mode.

        Args:
            ws: The WebSocket connection
            session: The client session
        """
        if session.mode == "chat":
            # History is retired with the mail model (R1b): the shared room
            # log is gone and R2 will serve the member's own inbox instead.
            await self._send_message(
                ws,
                {
                    "type": "history",
                    "messages": [],
                },
            )
            # Send participant list
            participants = self.client_gateway.get_all_members()
            await self._send_message(
                ws,
                {
                    "type": "participants",
                    "participants": [
                        {"id": p.id, "name": p.name, "type": p.type} for p in participants
                    ],
                },
            )

        elif session.mode == "agent":
            # Send agent statuses
            agents = self.agent_registry.get_all_agent_status()
            await self._send_message(
                ws,
                {
                    "type": "agents",
                    "agents": agents,
                },
            )

    # ==================== Send Helpers ====================

    async def _send_event(self, ws: web.WebSocketResponse, event: Event) -> None:
        """Send an event to a WebSocket client."""
        payload = event.payload
        if isinstance(payload, dict):
            # Mail delivery payloads nest a Mail under "mail"
            payload = {
                key: value.to_dict() if hasattr(value, "to_dict") else value
                for key, value in payload.items()
            }
        elif hasattr(payload, "to_dict"):
            payload = payload.to_dict()

        event_data = {
            "type": "event",
            "event_type": event.type.value,
            "payload": payload,
            "source": event.source,
            "timestamp": event.timestamp,
        }
        # Include tracing fields so clients can observe server-side trace filtering
        if event.trace_id:
            event_data["trace_id"] = event.trace_id
        if event.span_id:
            event_data["span_id"] = event.span_id
        if event.parent_span_id:
            event_data["parent_span_id"] = event.parent_span_id
        logger.debug("event_sending", event_type=event.type.value, ws_closed=ws.closed)
        await self._send_message(ws, event_data)

    async def _send_message(self, ws: web.WebSocketResponse, data: dict) -> None:
        """Send a message to a WebSocket client."""
        try:
            if not ws.closed:
                await ws.send_str(json.dumps(data, ensure_ascii=False, default=str))
        except Exception as e:
            logger.debug("websocket_send_error", error=str(e))

    async def _send_error(self, ws: web.WebSocketResponse, message: str) -> None:
        """Send an error message to a WebSocket client."""
        await self._send_message(
            ws,
            {
                "type": "error",
                "message": message,
            },
        )

    async def _send_message_to_session(self, session_id: str, data: dict) -> None:
        """Send a message to a specific session."""
        async with self._lock:
            ws = self._connections.get(session_id)

        if ws:
            await self._send_message(ws, data)

    async def _send_error_to_session(self, session_id: str, message: str) -> None:
        """Send an error message to a specific session."""
        await self._send_message_to_session(
            session_id,
            {
                "type": "error",
                "message": message,
            },
        )

    async def _send_pong(self, session_id: str) -> None:
        """Send a pong response to a ping."""
        await self._send_message_to_session(session_id, {"type": "pong"})

    async def broadcast(self, data: dict, exclude_session: str | None = None) -> None:
        """Broadcast a message to all connected clients.

        Args:
            data: The message to broadcast
            exclude_session: Optional session ID to exclude
        """
        async with self._lock:
            connections = list(self._connections.items())

        for session_id, ws in connections:
            if session_id != exclude_session:
                await self._send_message(ws, data)

    def get_connection_count(self) -> int:
        """Get the number of active WebSocket connections.

        Returns:
            Number of connections
        """
        return len(self._connections)


class WebSocketServer:
    """WebSocket server for Tlon.

    Wraps the aiohttp server with WebSocket support.

    Example:
        >>> ws_server = WebSocketServer(server, port=8888)
        >>> await ws_server.start()
        >>> # Server is running
        >>> await ws_server.stop()
    """

    def __init__(
        self,
        client_gateway,
        agent_registry,
        adapter_manager: AdapterManager,
        host: str = "127.0.0.1",
        port: int = 8888,
    ):
        """Initialize the WebSocket server.

        Args:
            client_gateway: The ClientGateway instance
            agent_registry: The AgentRegistry instance for agent operations
            adapter_manager: The AdapterManager for human identity and speaking
            host: Host to bind to
            port: Port to listen on
        """
        self.client_gateway = client_gateway
        self.agent_registry = agent_registry
        self.adapter_manager = adapter_manager
        self.host = host
        self.port = port
        self._handler = WebSocketHandler(client_gateway, agent_registry, adapter_manager)
        self._app: web.Application | None = None
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._running = False

    async def start(self) -> None:
        """Start the WebSocket server."""
        if self._running:
            logger.warning("websocket_server_already_running")
            return

        self._app = web.Application()
        self._app.router.add_get("/ws", self._handler.handle_websocket)

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()

        self._site = web.TCPSite(self._runner, self.host, self.port)
        await self._site.start()

        self._running = True
        logger.info("websocket_server_started", host=self.host, port=self.port)

    async def stop(self) -> None:
        """Stop the WebSocket server."""
        if not self._running:
            return

        logger.info("websocket_server_stopping")

        # Close all connections
        for session_id in list(self._handler._connections.keys()):
            await self.adapter_manager.detach(session_id)
            await self.client_gateway.disconnect_client(session_id)

        if self._runner:
            await self._runner.cleanup()

        self._running = False
        logger.info("websocket_server_stopped")

    def is_running(self) -> bool:
        """Check if the WebSocket server is running.

        Returns:
            True if running
        """
        return self._running

    def get_connection_count(self) -> int:
        """Get the number of active connections.

        Returns:
            Number of connections
        """
        return self._handler.get_connection_count()
