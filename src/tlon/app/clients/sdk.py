"""Tlon Client SDK.

Provides a unified client SDK for connecting to the Tlon Server.
Supports both WebSocket (real-time events) and REST API.

Example:
    >>> from tlon.clients.sdk import TlonClient
    >>> 
    >>> async with TlonClient("ws://localhost:8888/ws") as client:
    >>>     # Authenticate
    >>>     await client.authenticate("tui")
    >>>     
    >>>     # Set up event handler
    >>>     @client.on_event
    >>>     def handle_event(event):
    >>>         print(f"Received: {event}")
    >>>     
    >>>     # Send message
    >>>     await client.send_message("Hello!")
    >>>     
    >>>     # Change mode
    >>>     await client.set_mode("event")
"""

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

import aiohttp

from tlon.base.logger import get_logger

logger = get_logger(__name__)


@dataclass
class ServerConfig:
    """Server configuration.
    
    Attributes:
        ws_url: WebSocket URL (e.g., "ws://localhost:8888/ws")
        rest_url: REST API URL (e.g., "http://localhost:8080")
    """
    ws_url: str
    rest_url: str
    
    @classmethod
    def from_host(cls, host: str = "localhost", ws_port: int = 8888, rest_port: int = 8080):
        """Create config from host and ports.
        
        Args:
            host: Server host
            ws_port: WebSocket port
            rest_port: REST port
            
        Returns:
            ServerConfig instance
        """
        return cls(
            ws_url=f"ws://{host}:{ws_port}/ws",
            rest_url=f"http://{host}:{rest_port}",
        )


class TlonClient:
    """Client SDK for Tlon Server.
    
    Provides both WebSocket (real-time) and REST API access.
    
    Usage:
        >>> client = TlonClient("ws://localhost:8888/ws")
        >>> await client.connect()
        >>> await client.authenticate("tui")
        >>> 
        >>> # Or use async context manager
        >>> async with TlonClient("ws://localhost:8888/ws") as client:
        >>>     await client.authenticate("tui")
        >>>     ...
    """
    
    def __init__(
        self,
        ws_url: str,
        rest_url: Optional[str] = None,
        auto_reconnect: bool = True,
        reconnect_delay: float = 5.0,
    ):
        """Initialize the client.
        
        Args:
            ws_url: WebSocket server URL
            rest_url: Optional REST API URL
            auto_reconnect: Whether to auto-reconnect on disconnect
            reconnect_delay: Delay between reconnection attempts (seconds)
        """
        self.ws_url = ws_url
        self.rest_url = rest_url or ws_url.replace("ws://", "http://").replace("/ws", "")
        self.auto_reconnect = auto_reconnect
        self.reconnect_delay = reconnect_delay
        
        # Connection state
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._connected = False
        self._authenticated = False
        self._session_id: Optional[str] = None
        
        # Event handling
        self._event_handlers: List[Callable[[Dict], None]] = []
        self._message_handlers: List[Callable[[Dict], None]] = []
        self._error_handlers: List[Callable[[str], None]] = []
        self._receive_task: Optional[asyncio.Task] = None
        
        # State change callbacks
        self._on_connect: Optional[Callable[[], None]] = None
        self._on_disconnect: Optional[Callable[[], None]] = None
    
    # ==================== Connection Management ====================
    
    async def connect(self) -> bool:
        """Connect to the server.
        
        Returns:
            True if connected successfully
        """
        if self._connected:
            return True
        
        try:
            self._session = aiohttp.ClientSession()
            self._ws = await self._session.ws_connect(self.ws_url)
            self._connected = True
            
            # Start receiving messages
            self._receive_task = asyncio.create_task(self._receive_loop())
            
            if self._on_connect:
                self._on_connect()
            
            logger.info(f"Connected to {self.ws_url}")
            return True
        
        except Exception as e:
            logger.error(f"Failed to connect: {e}")
            await self._cleanup()
            return False
    
    async def disconnect(self) -> None:
        """Disconnect from the server."""
        self.auto_reconnect = False  # Disable auto-reconnect
        
        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
            self._receive_task = None
        
        if self._ws:
            await self._ws.close()
            self._ws = None
        
        await self._cleanup()
        
        logger.info("Disconnected from server")
    
    async def _cleanup(self) -> None:
        """Clean up resources."""
        self._connected = False
        self._authenticated = False
        self._session_id = None
        
        if self._session:
            await self._session.close()
            self._session = None
    
    async def _receive_loop(self) -> None:
        """Main receive loop for WebSocket messages."""
        try:
            while self._connected and self._ws:
                msg = await self._ws.receive()
                
                if msg.type == aiohttp.WSMsgType.TEXT:
                    await self._handle_message(msg.data)
                elif msg.type == aiohttp.WSMsgType.CLOSED:
                    logger.info("WebSocket closed by server")
                    break
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    logger.error(f"WebSocket error: {self._ws.exception()}")
                    break
        
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Receive loop error: {e}")
        finally:
            self._connected = False
            self._authenticated = False
            
            if self._on_disconnect:
                self._on_disconnect()
            
            # Auto-reconnect if enabled
            if self.auto_reconnect:
                logger.info(f"Reconnecting in {self.reconnect_delay}s...")
                await asyncio.sleep(self.reconnect_delay)
                await self.connect()
    
    async def _handle_message(self, data: str) -> None:
        """Handle an incoming WebSocket message.
        
        Args:
            data: JSON message data
        """
        try:
            msg = json.loads(data)
            msg_type = msg.get('type')
            
            if msg_type == 'auth_success':
                self._authenticated = True
                self._session_id = msg.get('session_id')
                logger.info(f"Authenticated as {self._session_id}")
            
            elif msg_type == 'event':
                event = {
                    'type': msg.get('event_type'),
                    'payload': msg.get('payload'),
                    'source': msg.get('source'),
                    'timestamp': msg.get('timestamp'),
                }
                logger.debug(f"[SDK] Received event: {event['type']}")
                for handler in self._event_handlers:
                    try:
                        if asyncio.iscoroutinefunction(handler):
                            # Create task and add exception handler
                            async def run_handler(h, e):
                                try:
                                    await h(e)
                                except Exception as ex:
                                    logger.error(f"Event handler error: {ex}", exc_info=True)
                            asyncio.create_task(run_handler(handler, event))
                        else:
                            handler(event)
                    except Exception as e:
                        logger.error(f"Event handler error: {e}", exc_info=True)
            
            elif msg_type == 'history':
                # History is sent as a message type event
                for handler in self._message_handlers:
                    try:
                        if asyncio.iscoroutinefunction(handler):
                            asyncio.create_task(handler(msg))
                        else:
                            handler(msg)
                    except Exception as e:
                        logger.error(f"Message handler error: {e}")
            
            elif msg_type == 'error':
                error_msg = msg.get('message', 'Unknown error')
                logger.error(f"Server error: {error_msg}")
                for handler in self._error_handlers:
                    try:
                        if asyncio.iscoroutinefunction(handler):
                            asyncio.create_task(handler(error_msg))
                        else:
                            handler(error_msg)
                    except Exception as e:
                        logger.error(f"Error handler error: {e}")
            
            elif msg_type == 'pong':
                pass  # Ping-pong is handled automatically
            
            else:
                # Pass to message handlers
                for handler in self._message_handlers:
                    try:
                        if asyncio.iscoroutinefunction(handler):
                            asyncio.create_task(handler(msg))
                        else:
                            handler(msg)
                    except Exception as e:
                        logger.error(f"Message handler error: {e}")
        
        except json.JSONDecodeError:
            logger.error(f"Invalid JSON received: {data}")
        except Exception as e:
            logger.error(f"Error handling message: {e}")
    
    async def _send(self, data: Dict) -> bool:
        """Send a message to the server.
        
        Args:
            data: Data to send
            
        Returns:
            True if sent successfully
        """
        if not self._connected or not self._ws:
            logger.error("Not connected to server")
            return False
        
        try:
            await self._ws.send_str(json.dumps(data, ensure_ascii=False))
            return True
        except Exception as e:
            logger.error(f"Failed to send message: {e}")
            return False
    
    # ==================== Authentication ====================
    
    async def authenticate(
        self,
        client_type: str,
        metadata: Optional[Dict] = None
    ) -> bool:
        """Authenticate with the server.
        
        Must be called after connect() and before other operations.
        
        Args:
            client_type: Client type ("tui", "web", "telegram")
            metadata: Optional metadata
            
        Returns:
            True if authenticated successfully
        """
        if not self._connected:
            logger.error("Must connect before authenticating")
            return False
        
        await self._send({
            'type': 'auth',
            'client_type': client_type,
            'metadata': metadata or {},
        })
        
        # Wait for auth response (with timeout)
        for _ in range(50):  # 5 second timeout
            if self._authenticated:
                return True
            await asyncio.sleep(0.1)
        
        logger.error("Authentication timeout")
        return False
    
    # ==================== Chat Operations ====================
    
    async def send_message(
        self,
        content: str,
        mentions: Optional[List[str]] = None
    ) -> bool:
        """Send a message to the chatroom.
        
        Args:
            content: Message content
            mentions: Optional list of agent IDs to mention
            
        Returns:
            True if sent successfully
        """
        return await self._send({
            'type': 'message',
            'content': content,
            'mentions': mentions or [],
        })
    
    # ==================== Mode & Filter Management ====================
    
    async def set_mode(self, mode: str) -> bool:
        """Set the operation mode.
        
        Args:
            mode: Mode ("chat", "event", "agent")
            
        Returns:
            True if successful
        """
        return await self._send({
            'type': 'mode',
            'mode': mode,
        })
    
    async def set_filters(self, filters: Dict[str, Any]) -> bool:
        """Set event filters.
        
        Args:
            filters: Filter dictionary (e.g., {"agent": "dev-*"})
            
        Returns:
            True if successful
        """
        return await self._send({
            'type': 'filter',
            'filters': filters,
        })
    
    # ==================== REST API ====================
    
    async def get_history(self, limit: int = 100) -> List[Dict]:
        """Get message history via REST API.
        
        Args:
            limit: Maximum number of messages
            
        Returns:
            List of messages
        """
        if not self._session:
            return []
        
        try:
            url = f"{self.rest_url}/history?limit={limit}"
            async with self._session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get('messages', [])
                else:
                    logger.error(f"Failed to get history: {resp.status}")
                    return []
        except Exception as e:
            logger.error(f"Failed to get history: {e}")
            return []
    
    async def get_agents(self) -> List[Dict]:
        """Get all agents via REST API.
        
        Returns:
            List of agent info
        """
        if not self._session:
            return []
        
        try:
            url = f"{self.rest_url}/agents"
            async with self._session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get('agents', [])
                else:
                    return []
        except Exception as e:
            logger.error(f"Failed to get agents: {e}")
            return []
    
    async def get_config(self) -> Dict:
        """Get server configuration via REST API.
        
        Returns:
            Configuration dictionary
        """
        if not self._session:
            return {}
        
        try:
            url = f"{self.rest_url}/config"
            async with self._session.get(url) as resp:
                if resp.status == 200:
                    return await resp.json()
                else:
                    return {}
        except Exception as e:
            logger.error(f"Failed to get config: {e}")
            return {}
    
    # ==================== Event Handlers ====================
    
    def on_event(self, handler: Callable[[Dict], None]) -> Callable:
        """Register an event handler.
        
        Args:
            handler: Callback function for events
            
        Returns:
            The handler (for use as decorator)
        """
        self._event_handlers.append(handler)
        return handler
    
    def on_message(self, handler: Callable[[Dict], None]) -> Callable:
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
    
    def on_connect(self, handler: Callable[[], None]) -> Callable:
        """Register a connect handler.
        
        Args:
            handler: Callback function called on connect
            
        Returns:
            The handler (for use as decorator)
        """
        self._on_connect = handler
        return handler
    
    def on_disconnect(self, handler: Callable[[], None]) -> Callable:
        """Register a disconnect handler.
        
        Args:
            handler: Callback function called on disconnect
            
        Returns:
            The handler (for use as decorator)
        """
        self._on_disconnect = handler
        return handler
    
    # ==================== Context Manager ====================
    
    async def __aenter__(self):
        """Async context manager entry."""
        await self.connect()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.disconnect()
    
    # ==================== Properties ====================
    
    @property
    def connected(self) -> bool:
        """Whether connected to the server."""
        return self._connected
    
    @property
    def authenticated(self) -> bool:
        """Whether authenticated with the server."""
        return self._authenticated
    
    @property
    def session_id(self) -> Optional[str]:
        """The session ID (if authenticated)."""
        return self._session_id
