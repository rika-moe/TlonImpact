"""WebSocket API for Tlon Server.

Provides real-time bidirectional communication:
- Server → Client: Events (messages, agent state changes)
- Client → Server: Commands (send message, change mode, etc.)

Protocol:
All messages are JSON with a "type" field:
- Client → Server:
  - {"type": "auth", "client_type": "tui", ...}
  - {"type": "message", "content": "...", "mentions": [...]}
  - {"type": "mode", "mode": "chat|event|agent"}
  - {"type": "filter", "filters": {...}}
  - {"type": "wake", "agent_id": "..."}
  - {"type": "ping"}
  
- Server → Client:
  - {"type": "event", "event_type": "...", "payload": {...}}
  - {"type": "auth_success", "session_id": "..."}
  - {"type": "error", "message": "..."}
  - {"type": "pong"}
"""

import asyncio
import json
from typing import Dict, Optional

from aiohttp import web, WSMsgType

from tlon.base.events.event import Event
from tlon.base.logger import get_logger
from tlon.organization.messaging.message import Message

logger = get_logger(__name__)


class WebSocketHandler:
    """Handler for WebSocket connections.
    
    Manages client connections and handles bidirectional communication.
    
    Example:
        >>> ws_handler = WebSocketHandler(server)
        >>> app = web.Application()
        >>> app.router.add_get('/ws', ws_handler.handle_websocket)
    """
    
    def __init__(self, server):
        """Initialize the WebSocket handler.
        
        Args:
            server: The Tlon Server instance
        """
        self.server = server
        self._connections: Dict[str, web.WebSocketResponse] = {}
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
        
        session_id: Optional[str] = None
        
        try:
            # Wait for authentication message
            auth_result = await self._authenticate(ws)
            if not auth_result:
                await ws.close(code=4001, message=b'Authentication failed')
                return ws
            
            session, session_id = auth_result
            
            # Store connection
            async with self._lock:
                self._connections[session_id] = ws
            
            logger.info(f"WebSocket connected: {session_id}")
            
            # Send initial data based on mode
            await self._send_initial_data(ws, session)
            
            # Handle incoming messages
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    await self._handle_client_message(session_id, msg.data)
                elif msg.type == WSMsgType.ERROR:
                    logger.error(f"WebSocket error for {session_id}: {ws.exception()}")
                elif msg.type == WSMsgType.CLOSE:
                    break
        
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"WebSocket error: {e}", exc_info=True)
        finally:
            # Cleanup
            if session_id:
                async with self._lock:
                    self._connections.pop(session_id, None)
                await self.server.disconnect_client(session_id)
                logger.info(f"WebSocket disconnected: {session_id}")
        
        return ws
    
    async def _authenticate(
        self,
        ws: web.WebSocketResponse
    ) -> Optional[tuple]:
        """Authenticate a new WebSocket connection.
        
        Waits for an auth message from the client.
        
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
            
            if data.get('type') != 'auth':
                await self._send_error(ws, "Expected auth message")
                return None
            
            client_type = data.get('client_type', 'unknown')
            metadata = data.get('metadata', {})
            
            # Define event handler for this session
            async def event_handler(event: Event) -> None:
                await self._send_event(ws, event)
            
            # Connect client to server
            session = await self.server.connect_client(
                client_type=client_type,
                event_handler=event_handler,
                metadata=metadata,
            )
            
            # Send auth success
            await self._send_message(ws, {
                "type": "auth_success",
                "session_id": session.id,
                "timestamp": session.connected_at,
            })
            
            return session, session.id
        
        except asyncio.TimeoutError:
            await self._send_error(ws, "Authentication timeout")
            return None
        except json.JSONDecodeError:
            await self._send_error(ws, "Invalid JSON")
            return None
        except Exception as e:
            logger.error(f"Authentication error: {e}")
            await self._send_error(ws, f"Authentication failed: {str(e)}")
            return None
    
    async def _handle_client_message(self, session_id: str, data: str) -> None:
        """Handle a message from a client.
        
        Args:
            session_id: The client's session ID
            data: The message data (JSON string)
        """
        try:
            msg = json.loads(data)
            msg_type = msg.get('type')
            
            if msg_type == 'ping':
                await self._send_pong(session_id)
            
            elif msg_type == 'message':
                await self._handle_send_message(session_id, msg)
            
            elif msg_type == 'mode':
                await self._handle_mode_change(session_id, msg)
            
            elif msg_type == 'filter':
                await self._handle_filter_change(session_id, msg)
            
            elif msg_type == 'wake':
                await self._handle_wake_agent(session_id, msg)
            
            elif msg_type == 'stop':
                await self._handle_stop_agent(session_id, msg)
            
            else:
                await self._send_error_to_session(session_id, f"Unknown message type: {msg_type}")
        
        except json.JSONDecodeError:
            await self._send_error_to_session(session_id, "Invalid JSON")
        except Exception as e:
            logger.error(f"Error handling client message: {e}")
            await self._send_error_to_session(session_id, f"Error: {str(e)}")
    
    async def _handle_send_message(self, session_id: str, msg: Dict) -> None:
        """Handle a send message request from client."""
        content = msg.get('content', '').strip()
        if not content:
            await self._send_error_to_session(session_id, "Message content is empty")
            return
        
        mentions = msg.get('mentions', [])
        
        # Send message through server
        await self.server.send_message(
            content=content,
            sender_id="human",
            sender_name="人类",
            sender_type="human",
            mentions=mentions,
        )
    
    async def _handle_mode_change(self, session_id: str, msg: Dict) -> None:
        """Handle a mode change request from client."""
        mode = msg.get('mode')
        if mode not in ('chat', 'event', 'agent'):
            await self._send_error_to_session(session_id, f"Invalid mode: {mode}")
            return
        
        success = await self.server.update_session_mode(session_id, mode)
        if success:
            await self._send_message_to_session(session_id, {
                "type": "mode_changed",
                "mode": mode,
            })
            # Send initial data for new mode
            session = self.server.get_session(session_id)
            if session:
                ws = self._connections.get(session_id)
                if ws:
                    await self._send_initial_data(ws, session)
        else:
            await self._send_error_to_session(session_id, "Failed to change mode")
    
    async def _handle_filter_change(self, session_id: str, msg: Dict) -> None:
        """Handle a filter change request from client."""
        filters = msg.get('filters', {})
        
        success = await self.server.update_session_filters(session_id, filters)
        if success:
            await self._send_message_to_session(session_id, {
                "type": "filters_updated",
                "filters": filters,
            })
        else:
            await self._send_error_to_session(session_id, "Failed to update filters")
    
    async def _handle_wake_agent(self, session_id: str, msg: Dict) -> None:
        """Handle a wake agent request from client."""
        agent_id = msg.get('agent_id')
        if not agent_id:
            await self._send_error_to_session(session_id, "agent_id is required")
            return
        
        success = await self.server.wake_agent(agent_id)
        await self._send_message_to_session(session_id, {
            "type": "wake_result",
            "agent_id": agent_id,
            "success": success,
        })
    
    async def _handle_stop_agent(self, session_id: str, msg: Dict) -> None:
        """Handle a stop agent request from client."""
        agent_id = msg.get('agent_id')
        if not agent_id:
            await self._send_error_to_session(session_id, "agent_id is required")
            return
        
        success = await self.server.stop_agent(agent_id)
        await self._send_message_to_session(session_id, {
            "type": "stop_result",
            "agent_id": agent_id,
            "success": success,
        })
    
    async def _send_initial_data(
        self,
        ws: web.WebSocketResponse,
        session
    ) -> None:
        """Send initial data to a client based on their mode.
        
        Args:
            ws: The WebSocket connection
            session: The client session
        """
        if session.mode == 'chat':
            # Send recent history
            history = await self.server.get_history(limit=50)
            await self._send_message(ws, {
                "type": "history",
                "messages": [msg.to_dict() for msg in history],
            })
            # Send participant list
            participants = self.server.chatroom.get_all_participants()
            await self._send_message(ws, {
                "type": "participants",
                "participants": [
                    {"id": p.id, "name": p.name, "type": p.type}
                    for p in participants
                ],
            })
        
        elif session.mode == 'agent':
            # Send agent statuses
            agents = self.server.get_all_agent_statuses()
            await self._send_message(ws, {
                "type": "agents",
                "agents": agents,
            })
    
    # ==================== Send Helpers ====================
    
    async def _send_event(self, ws: web.WebSocketResponse, event: Event) -> None:
        """Send an event to a WebSocket client."""
        payload = event.payload
        if isinstance(payload, Message):
            payload = payload.to_dict()
        elif hasattr(payload, 'to_dict'):
            payload = payload.to_dict()
        
        event_data = {
            "type": "event",
            "event_type": event.type.value,
            "payload": payload,
            "source": event.source,
            "timestamp": event.timestamp,
        }
        logger.debug(f"[WebSocket] Sending event {event.type.value} to client")
        await self._send_message(ws, event_data)
    
    async def _send_message(self, ws: web.WebSocketResponse, data: Dict) -> None:
        """Send a message to a WebSocket client."""
        try:
            if not ws.closed:
                await ws.send_str(json.dumps(data, ensure_ascii=False, default=str))
        except Exception as e:
            logger.debug(f"Error sending WebSocket message: {e}")
    
    async def _send_error(self, ws: web.WebSocketResponse, message: str) -> None:
        """Send an error message to a WebSocket client."""
        await self._send_message(ws, {
            "type": "error",
            "message": message,
        })
    
    async def _send_message_to_session(self, session_id: str, data: Dict) -> None:
        """Send a message to a specific session."""
        async with self._lock:
            ws = self._connections.get(session_id)
        
        if ws:
            await self._send_message(ws, data)
    
    async def _send_error_to_session(self, session_id: str, message: str) -> None:
        """Send an error message to a specific session."""
        await self._send_message_to_session(session_id, {
            "type": "error",
            "message": message,
        })
    
    async def _send_pong(self, session_id: str) -> None:
        """Send a pong response to a ping."""
        await self._send_message_to_session(session_id, {"type": "pong"})
    
    async def broadcast(self, data: Dict, exclude_session: Optional[str] = None) -> None:
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
        server,
        host: str = "127.0.0.1",
        port: int = 8888,
    ):
        """Initialize the WebSocket server.
        
        Args:
            server: The Tlon Server instance
            host: Host to bind to
            port: Port to listen on
        """
        self.server = server
        self.host = host
        self.port = port
        self._handler = WebSocketHandler(server)
        self._app: Optional[web.Application] = None
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None
        self._running = False
    
    async def start(self) -> None:
        """Start the WebSocket server."""
        if self._running:
            logger.warning("WebSocket server already running")
            return
        
        self._app = web.Application()
        self._app.router.add_get('/ws', self._handler.handle_websocket)
        
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        
        self._site = web.TCPSite(self._runner, self.host, self.port)
        await self._site.start()
        
        self._running = True
        logger.info(f"WebSocket server started on ws://{self.host}:{self.port}/ws")
    
    async def stop(self) -> None:
        """Stop the WebSocket server."""
        if not self._running:
            return
        
        logger.info("Stopping WebSocket server...")
        
        # Close all connections
        for session_id in list(self._handler._connections.keys()):
            await self.server.disconnect_client(session_id)
        
        if self._runner:
            await self._runner.cleanup()
        
        self._running = False
        logger.info("WebSocket server stopped")
    
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
