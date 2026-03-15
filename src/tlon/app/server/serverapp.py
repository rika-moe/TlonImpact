"""Tlon Server Application with full Dependency Injection.

Main entry point for the server process. ServerApp acts as the component
assembler, creating all core components and injecting them into ClientGateway.

Usage:
    >>> from tlon.app.server.serverapp import ServerApp
    >>> app = ServerApp()
    >>> await app.initialize()  # DI assembly, load config, setup components
    >>> await app.start()       # Start servers, block until shutdown
    >>> await app.shutdown()    # Graceful shutdown
"""

import asyncio
import signal
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any

from .core.client_gateway import ClientGateway
from .api.rest import RESTServer
from .api.websocket import WebSocketServer
from .config_manager import ConfigManager
from tlon.base.config import AgentConfig, ConfigLoader, DockerRuntimeConfig, LLMConfig
from tlon.subsystem.memory import MemoryConfig
from tlon.base.events.event_bus import EventBus
from tlon.organization.messaging.store import FileMessageStore
from tlon.organization.messaging.router import MessageRouter
from tlon.organization.positions import PositionRegistry
from tlon.agents.agent_registry import AgentRegistry
from tlon.base.settings import settings, get_tlon_home
from tlon.base.logger import get_logger, configure_logging

logger = get_logger(__name__)


class ServerApp:
    """Server application with full Dependency Injection.
    
    ServerApp is responsible for:
    - Creating all core components (DI container)
    - Managing component lifecycle (initialize → start → shutdown)
    - Coordinating graceful shutdown
    
    Example:
        >>> app = ServerApp(host="0.0.0.0", rest_port=8080, ws_port=8888)
        >>> await app.initialize()  # DI assembly
        >>> await app.start()       # Start serving
        >>> await app.shutdown()    # Cleanup
    """
    
    def __init__(
        self,
        host: str = "127.0.0.1",
        rest_port: int = 8080,
        ws_port: int = 8888,
        data_dir: Optional[Path] = None,
        config_dir: Optional[Path] = None,
    ):
        """Initialize the server application (does not create components yet).
        
        Args:
            host: Host to bind servers to
            rest_port: Port for REST API
            ws_port: Port for WebSocket API
            data_dir: Directory for persistence
            config_dir: Directory containing position and agent configs
        """
        self.host = host
        self.rest_port = rest_port
        self.ws_port = ws_port
        self.data_dir = data_dir or settings.data_dir
        self.config_dir = config_dir or (get_tlon_home() / "config")
        
        # Config subdirectories
        self.positions_dir = self.config_dir / "positions"
        self.agents_dir = self.config_dir / "agents"
        
        # DI Container - all components created in initialize()
        self._components: Dict[str, Any] = {}
        
        # Subsystem references (for convenient access)
        self.client_gateway: Optional[ClientGateway] = None
        self.rest: Optional[RESTServer] = None
        self.websocket: Optional[WebSocketServer] = None
        
        # State
        self._shutdown_event = asyncio.Event()
        self._running = False
    
    async def initialize(self) -> None:
        """Initialize all components with full Dependency Injection.
        
        This is the DI assembly phase where all components are created
        and wired together. No network ports are opened yet.
        """
        logger.info("Initializing Tlon Server with full DI...")
        
        # ========== Step 1: Configuration Layer ==========
        config_loader = ConfigLoader(
            positions_dir=str(self.positions_dir),
            agents_dir=str(self.agents_dir),
        )
        self._components["config_loader"] = config_loader
        
        config_manager = ConfigManager(config_loader)
        await config_manager.initial_load()
        self._components["config_manager"] = config_manager
        
        # ========== Step 2: Core Components (all created in ServerApp) ==========
        
        # 2.1 EventBus - event bus for inter-component communication
        event_bus = EventBus()
        await event_bus.start()
        self._components["event_bus"] = event_bus
        logger.info("EventBus started")
        
        # 2.2 MessageStore - storage for messages (stateful)
        message_store = FileMessageStore(
            data_dir=self.data_dir / "chat_history",
            filename="messages.jsonl"
        )
        await message_store.initialize()
        self._components["message_store"] = message_store
        logger.info("MessageStore initialized")
        
        # 2.3 MessageRouter - stateless message routing
        message_router = MessageRouter(
            event_bus=event_bus,
            message_store=message_store,
            agent_registry=None,  # Will be set after AgentRegistry is created
        )
        await message_router.start()
        self._components["message_router"] = message_router
        logger.info("MessageRouter started")
        
        # 2.4 PositionRegistry - position template registry
        position_registry = PositionRegistry()
        for position in config_manager.positions.values():
            position_registry.register(position)
        self._components["position_registry"] = position_registry
        logger.info(f"Loaded {len(position_registry.get_all())} positions")
        
        # 2.5 AgentRegistry - agent management (depends on EventBus, PositionRegistry, MessageStore)
        agent_registry = AgentRegistry(
            event_bus=event_bus,  # DI
            position_registry=position_registry,  # DI
            message_store=message_store,  # DI - Agents can now query history!
        )
        # Register initial agents
        for agent_config in config_manager.agents.values():
            agent_registry.register(agent_config)
        
        # Now that AgentRegistry is created, update router with it
        message_router._agent_registry = agent_registry
        
        await agent_registry.initialize()
        self._components["agent_registry"] = agent_registry
        logger.info(f"Loaded {len(agent_registry.get_all())} agents")
        
        # ========== Step 3: ClientGateway ==========
        self.client_gateway = ClientGateway(
            # Core components (injected)
            event_bus=event_bus,
            message_store=message_store,
            agent_registry=agent_registry,
            position_registry=position_registry,
            # Configuration management
            config_manager=config_manager,
            # Other parameters
            data_dir=self.data_dir,
            host=self.host,
            port=self.ws_port,
        )
        await self.client_gateway.initialize()
        logger.info("ClientGateway initialized")

        # ========== Step 4: API Layer (DI) ==========
        self.rest = RESTServer(
            self.client_gateway,
            agent_registry=agent_registry,  # Direct access for agent operations
            config_manager=config_manager,  # For /admin endpoints
            host=self.host,
            port=self.rest_port,
        )
        self.websocket = WebSocketServer(
            self.client_gateway,
            agent_registry=agent_registry,  # Direct access for agent operations
            host=self.host,
            port=self.ws_port,
        )
        
        self._running = False
        logger.info("ServerApp initialized successfully")
        logger.info(f"  Positions: {len(position_registry.get_all())}")
        logger.info(f"  Agents: {len(agent_registry.get_all())}")
        logger.info(f"  REST API: http://{self.host}:{self.rest_port}")
        logger.info(f"  WebSocket: ws://{self.host}:{self.ws_port}/ws")
    
    def _get_initial_agents(self, config_manager: ConfigManager) -> List[AgentConfig]:
        """Get initial agent configurations.
        
        Returns agents from config files, or defaults if none exist.
        """
        agents = config_manager.agents
        if agents:
            logger.info(f"Loaded {len(agents)} agents from config")
            return list(agents.values())
        
        logger.warning("No agent configs found, using defaults")
        return self._create_default_configs()
    
    def _create_default_configs(self) -> List[AgentConfig]:
        """Create default agent configurations."""
        logger.info("Creating default agent configuration")
        
        skills_dir = str(get_tlon_home() / "skills")
        
        return [AgentConfig(
            id="dev-001",
            name="林知遥",
            personality="技术能力强、注重细节、喜欢钻研",
            position_id="developer",
            llm=LLMConfig(
                base_url=settings.llm_base_url,
                auth_token=settings.llm_api_key,
                model_name=settings.llm_model_name,
                temperature=settings.llm_temperature,
            ),
            docker=DockerRuntimeConfig(
                image=settings.docker_image,
                memory_limit=settings.docker_memory_limit,
                skills_dir=skills_dir,
            ),
            memory=MemoryConfig(agent_id="dev-001"),
            skills=[],
            respond_to_all=False,
        )]

    # ========== Lifecycle Methods ==========
    
    async def start(self) -> None:
        """Start the server and block until shutdown.
        
        This starts all API servers and waits for a shutdown signal.
        """
        if self._running:
            logger.warning("Server already running")
            return
        
        self._running = True
        
        # Start API servers
        await self.rest.start()
        await self.websocket.start()
        
        logger.info("=" * 50)
        logger.info("🚀 Tlon Server is running!")
        logger.info("=" * 50)
        logger.info(f"REST API:    http://{self.host}:{self.rest_port}")
        logger.info(f"WebSocket:   ws://{self.host}:{self.ws_port}/ws")
        logger.info("")
        logger.info("Press Ctrl+C to stop")
        logger.info("=" * 50)
        
        # Wait for shutdown signal
        await self._shutdown_event.wait()
        
        logger.info("Shutdown signal received...")
    
    async def shutdown(self) -> None:
        """Shutdown the server gracefully.
        
        Stops API servers and shuts down all components in reverse order.
        """
        if not self._running and not self._components:
            return
        
        logger.info("Shutting down Tlon Server...")
        
        # Stop API servers
        if self.websocket:
            await self.websocket.stop()
        if self.rest:
            await self.rest.stop()
        
        # Shutdown ClientGateway
        if self.client_gateway:
            await self.client_gateway.shutdown()
        
        # Shutdown core components (reverse order of creation)
        agent_registry = self._components.get("agent_registry")
        if agent_registry:
            await agent_registry.shutdown()
        
        message_router = self._components.get("message_router")
        if message_router:
            await message_router.stop()
        
        event_bus = self._components.get("event_bus")
        if event_bus:
            await event_bus.stop()
        
        self._running = False
        self._shutdown_event.set()
        
        logger.info("Tlon Server stopped")
    
    def handle_signal(self, sig: signal.Signals) -> None:
        """Handle shutdown signals.
        
        Args:
            sig: The signal that was received
        """
        logger.info(f"Received signal {sig.name}, initiating shutdown...")
        asyncio.create_task(self.shutdown())


async def run_server(
    host: str = "127.0.0.1",
    rest_port: int = 8080,
    ws_port: int = 8888,
    log_level: str = "INFO",
    debug: bool = False,
) -> int:
    """Run the Tlon server.
    
    This is the main entry point for the server process.
    
    Args:
        host: Host to bind to
        rest_port: Port for REST API
        ws_port: Port for WebSocket API
        log_level: Logging level
        debug: Enable debug mode
        
    Returns:
        Exit code (0 for success)
    """
    # Configure logging
    configure_logging(log_level=log_level, debug=debug)
    
    logger.info("Starting Tlon Server...")
    logger.info(f"  Host: {host}")
    logger.info(f"  REST Port: {rest_port}")
    logger.info(f"  WebSocket Port: {ws_port}")
    
    # Create and run server
    app = ServerApp(host=host, rest_port=rest_port, ws_port=ws_port)
    
    # Setup signal handlers
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda s=sig: app.handle_signal(s))
    
    try:
        await app.initialize()
        await app.start()
        return 0
    except Exception as e:
        logger.error(f"Server error: {e}", exc_info=True)
        return 1
    finally:
        await app.shutdown()
