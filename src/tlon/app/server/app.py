"""Tlon Server Application.

Main entry point for the server process. Combines:
- Core server logic (TlonServer)
- REST API (RESTServer)
- WebSocket API (WebSocketServer)

Usage:
    >>> from tlon.server.app import ServerApp
    >>> app = ServerApp()
    >>> await app.initialize()
    >>> await app.start()
    >>> # Server is running
    >>> await app.shutdown()
"""

import asyncio
import signal
from pathlib import Path
from typing import List, Optional

import yaml

from .core.server import TlonServer
from .api.rest import RESTServer
from .api.websocket import WebSocketServer
from tlon.base.config.config import AgentConfig, DockerRuntimeConfig, LLMConfig
from tlon.subsystem.memory import MemoryConfig
from tlon.organization.positions import PositionRegistry
from tlon.base.settings import settings
from tlon.base.logger import get_logger, configure_logging

logger = get_logger(__name__)


class ServerApp:
    """Server application combining REST API and WebSocket.
    
    This is the main entry point for running the Tlon server.
    
    Example:
        >>> app = ServerApp(host="0.0.0.0", rest_port=8080, ws_port=8888)
        >>> await app.initialize()  # Load config, setup components
        >>> await app.start()       # Start servers, block until shutdown
        >>> await app.shutdown()    # Graceful shutdown
    """
    
    def __init__(
        self,
        host: str = "127.0.0.1",
        rest_port: int = 8080,
        ws_port: int = 8888,
        data_dir: Optional[Path] = None,
        config_dir: Optional[Path] = None,
    ):
        """Initialize the server application.
        
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
        self.data_dir = data_dir or settings.app.data_dir
        # __file__ is src/tlon/server/app.py
        # parent.parent.parent.parent gives us the project root
        self.config_dir = config_dir or (
            Path(__file__).parent.parent.parent.parent / "config"
        )
        
        # Config subdirectories
        self.positions_dir = self.config_dir / "positions"
        self.agents_dir = self.config_dir / "agents"
        
        # Server components
        self.core: Optional[TlonServer] = None
        self.rest: Optional[RESTServer] = None
        self.websocket: Optional[WebSocketServer] = None
        
        # Position registry (new architecture)
        self.position_registry: Optional[PositionRegistry] = None
        
        # State
        self._shutdown_event = asyncio.Event()
        self._running = False
    
    async def initialize(self) -> None:
        """Initialize all server components.
        
        This loads configuration and prepares the server to start,
        but does not start listening for connections yet.
        """
        logger.info("Initializing Tlon Server...")
        
        # Step 1: Load positions (new architecture)
        self.position_registry = PositionRegistry()
        if self.positions_dir.exists():
            self.position_registry.load_from_directory(self.positions_dir)
            logger.info(f"Loaded {len(self.position_registry.get_all())} positions")
        else:
            logger.warning(f"Positions directory not found: {self.positions_dir}")
        
        # Step 2: Create core server (with position registry)
        self.core = TlonServer(
            host=self.host,
            port=self.ws_port,
            data_dir=self.data_dir,
            position_registry=self.position_registry,
        )
        
        # Step 3: Initialize core components (EventBus, ChatRoom, etc.)
        await self.core.initialize()
        
        # Step 4: Load and register agents
        agent_configs = self._load_agent_configs()
        self.core.register_agents(agent_configs)
        await self.core.initialize_agents()
        
        # Step 5: Create API servers
        self.rest = RESTServer(self.core, host=self.host, port=self.rest_port)
        self.websocket = WebSocketServer(self.core, host=self.host, port=self.ws_port)
        
        logger.info("Tlon Server initialized successfully")
        logger.info(f"  Positions: {len(self.position_registry.get_all()) if self.position_registry else 0}")
        logger.info(f"  Agents registered: {len(agent_configs)}")
        logger.info(f"  REST API: http://{self.host}:{self.rest_port}")
        logger.info(f"  WebSocket: ws://{self.host}:{self.ws_port}/ws")
    
    async def start(self) -> None:
        """Start the server and block until shutdown.
        
        This starts all servers and waits for a shutdown signal.
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
        """Shutdown the server gracefully."""
        if not self._running:
            return
        
        logger.info("Shutting down Tlon Server...")
        
        # Stop API servers
        if self.websocket:
            await self.websocket.stop()
        if self.rest:
            await self.rest.stop()
        
        # Shutdown core server
        if self.core:
            await self.core.shutdown()
        
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
    
    def _load_agent_configs(self) -> List[AgentConfig]:
        """Load agent configurations.
        
        Returns:
            List of AgentConfig objects
        """
        if self.agents_dir.exists():
            configs = self._load_agents_from_directory(self.agents_dir)
            if configs:
                logger.info(f"Loaded {len(configs)} agents from {self.agents_dir}")
                return configs
        
        logger.warning("No agent configs found, using defaults")
        return self._create_default_configs()
    
    def _load_agents_from_directory(self, directory: Path) -> List[AgentConfig]:
        """Load agent configs from directory.
        
        Args:
            directory: Directory containing agent YAML files
            
        Returns:
            List of AgentConfig objects
        """
        configs = []
        
        for yaml_file in sorted(directory.glob("*.yaml")):
            try:
                with open(yaml_file, 'r', encoding='utf-8') as f:
                    data = yaml.safe_load(f)
                
                if not data or 'agent' not in data:
                    logger.warning(f"No 'agent' key in {yaml_file}")
                    continue
                
                agent_config = self._build_agent_config(data['agent'])
                configs.append(agent_config)
                logger.debug(f"Loaded agent from {yaml_file}: {agent_config.id}")
                
            except Exception as e:
                logger.error(f"Failed to load agent from {yaml_file}: {e}")
        
        return configs
    
    def _build_agent_config(self, data: dict) -> AgentConfig:
        """Build AgentConfig from YAML data.
        
        Args:
            data: Agent configuration data
            
        Returns:
            AgentConfig object
        """
        agent_id = data.get('id', 'dev-001')
        position_id = data.get('position_id', 'developer')
        
        # Get position info if available
        position = None
        if self.position_registry:
            position = self.position_registry.get(position_id)
        
        llm_data = data.get('llm', {})
        llm_config = LLMConfig(
            base_url=llm_data.get('base_url', settings.llm.base_url),
            auth_token=settings.llm.auth_token,
            model_name=llm_data.get('model_name', settings.llm.model_name),
            temperature=llm_data.get('temperature', settings.llm.temperature),
        )
        
        docker_data = data.get('docker', {})
        skills_dir = str(Path(__file__).parent.parent.parent.parent / "skills")
        docker_config = DockerRuntimeConfig(
            image=docker_data.get('image', settings.docker.image),
            memory_limit=docker_data.get('memory_limit', settings.docker.memory_limit),
            skills_dir=skills_dir,
        )
        
        memory_data = data.get('memory', {})
        memory_config = MemoryConfig(
            agent_id=agent_id,
            working_max_items=memory_data.get('max_history', 10),
            working_summary_window=memory_data.get('summary_window', 10),
            episodic_max_summaries=memory_data.get('max_summaries', 20),
            episodic_max_facts=memory_data.get('max_facts', 50),
        )
        
        return AgentConfig(
            id=agent_id,
            name=data.get('name', '开发-A'),
            personality=data.get('personality', '技术能力强、注重细节'),
            position_id=position_id,
            llm=llm_config,
            docker=docker_config,
            memory=memory_config,
            skills=data.get('skills', position.default_skills if position else []),
            respond_to_all=False,
        )
    
    def _create_default_configs(self) -> List[AgentConfig]:
        """Create default agent configurations.
        
        Returns:
            List with a single default agent config
        """
        logger.info("Creating default agent configuration")
        
        skills_dir = str(Path(__file__).parent.parent.parent.parent / "skills")
        
        return [AgentConfig(
            id="dev-001",
            name="林知遥",
            personality="技术能力强、注重细节、喜欢钻研",
            position_id="developer",
            llm=LLMConfig(
                base_url=settings.llm.base_url,
                auth_token=settings.llm.auth_token,
                model_name=settings.llm.model_name,
                temperature=settings.llm.temperature,
            ),
            docker=DockerRuntimeConfig(
                image=settings.docker.image,
                memory_limit=settings.docker.memory_limit,
                skills_dir=skills_dir,
            ),
            memory=MemoryConfig(agent_id="dev-001"),
            skills=[],
            respond_to_all=False,
        )]


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
