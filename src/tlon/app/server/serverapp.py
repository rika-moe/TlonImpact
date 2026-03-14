"""Tlon Server Application with full Dependency Injection.

Main entry point for the server process. ServerApp acts as the component
assembler, creating all core components and injecting them into TlonServer.

Usage:
    >>> from tlon.server.app import ServerApp
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

from .core.server import TlonServer
from .api.rest import RESTServer
from .api.websocket import WebSocketServer
from .config_manager import ConfigManager
from tlon.base.config import (
    AgentConfig,
    ConfigLoader,
    DockerRuntimeConfig,
    LLMConfig,
    PositionConfig,
)
from tlon.subsystem.memory import MemoryConfig
from tlon.organization.messaging import EventBus, Congress, CongressMetadata
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
    - Handling configuration reloads
    - Coordinating graceful shutdown
    
    All components are created in initialize() and injected into TlonServer,
    which acts as a pure coordinator without creating any components.
    
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
        self.core: Optional[TlonServer] = None
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
        
        # 2.2 Congress - chat room (depends on EventBus)
        congress_metadata = CongressMetadata(
            id="main-room",
            name="主聊天室",
            created_at=datetime.now().isoformat(),
            data_dir=self.data_dir / "chat_history",
        )
        congress = Congress(
            metadata=congress_metadata,
            event_bus=event_bus,  # DI
        )
        await congress.initialize()
        self._components["congress"] = congress
        logger.info(f"Congress '{congress_metadata.name}' initialized")
        
        # 2.3 PositionRegistry - position template registry
        position_registry = PositionRegistry()
        for position in config_manager.positions.values():
            position_registry.register(position)
        self._components["position_registry"] = position_registry
        logger.info(f"Loaded {len(position_registry.get_all())} positions")
        
        # 2.4 AgentRegistry - agent management (depends on EventBus, Congress, PositionRegistry)
        agent_registry = AgentRegistry(
            event_bus=event_bus,  # DI
            chatroom=congress,     # DI (AgentRegistry uses 'chatroom' as param name)
            position_registry=position_registry,  # DI
        )
        # Register initial agents
        initial_agents = self._get_initial_agents(config_manager)
        for agent_config in initial_agents:
            agent_registry.register(agent_config)
        await agent_registry.initialize()
        self._components["agent_registry"] = agent_registry
        logger.info(f"Loaded {len(agent_registry.get_all())} agents")
        
        # ========== Step 3: TlonServer (pure coordinator, receives all dependencies) ==========
        self.core = TlonServer(
            # Core components (injected)
            event_bus=event_bus,
            congress=congress,
            position_registry=position_registry,
            agent_registry=agent_registry,  # Injected from ServerApp
            # Configuration management
            config_manager=config_manager,
            # Other parameters
            data_dir=self.data_dir,
            host=self.host,
            port=self.ws_port,
        )
        await self.core.initialize()
        logger.info("TlonServer initialized as coordinator")
        
        # ========== Step 4: Register config reload callbacks ==========
        config_manager.on_positions_changed(self._on_positions_reloaded)
        config_manager.on_agents_changed(self._on_agents_reloaded)
        logger.info("Config reload callbacks registered")
        
        # ========== Step 5: API Layer (DI) ==========
        self.rest = RESTServer(
            self.core,
            config_manager=config_manager,  # For /admin/reload endpoints
            host=self.host,
            port=self.rest_port,
        )
        self.websocket = WebSocketServer(
            self.core,
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
    
    # ========== Config Reload Handlers ==========
    
    async def _on_positions_reloaded(self, new_positions: Dict[str, PositionConfig]) -> None:
        """Handle position configuration reload.
        
        Args:
            new_positions: New position configurations
        """
        logger.info(f"Reloading {len(new_positions)} positions...")
        
        position_registry = self._components["position_registry"]
        
        # Update PositionRegistry
        position_registry.clear()
        for position in new_positions.values():
            position_registry.register(position)
        
        # Notify AgentRegistry to update position references
        agent_registry = self._components["agent_registry"]
        await agent_registry.update_position_references(new_positions)
        
        # Notify TlonServer to broadcast to clients
        await self.core.on_positions_changed(new_positions)
    
    async def _on_agents_reloaded(self, new_agents: Dict[str, AgentConfig]) -> None:
        """Handle agent configuration reload.
        
        Args:
            new_agents: New agent configurations
        """
        logger.info(f"Reloading {len(new_agents)} agents...")
        
        agent_registry = self._components["agent_registry"]
        
        # Calculate differences
        current_ids = set(agent_registry.get_all_ids())
        new_ids = set(new_agents.keys())
        
        # Remove agents that no longer exist
        for agent_id in current_ids - new_ids:
            logger.info(f"Removing agent: {agent_id}")
            await agent_registry.unregister(agent_id)
        
        # Add or update agents
        for agent_id in new_ids:
            if agent_id not in current_ids:
                # New agent
                logger.info(f"Adding new agent: {agent_id}")
                await agent_registry.register(new_agents[agent_id])
            else:
                # Check if changed
                old_config = agent_registry.get_config(agent_id)
                if old_config != new_agents[agent_id]:
                    logger.info(f"Updating agent: {agent_id}")
                    await agent_registry.update_config(agent_id, new_agents[agent_id])
        
        # Notify TlonServer to broadcast to clients
        await self.core.on_agents_changed(new_agents)
    
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
        
        # Shutdown TlonServer (coordinator)
        if self.core:
            await self.core.shutdown()
        
        # Shutdown core components (reverse order of creation)
        agent_registry = self._components.get("agent_registry")
        if agent_registry:
            await agent_registry.shutdown()
        
        congress = self._components.get("congress")
        if congress:
            await congress.shutdown()
        
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
