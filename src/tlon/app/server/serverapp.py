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
from pathlib import Path
from typing import Any

from tlon.agents.agent_registry import AgentRegistry
from tlon.base.config import AgentConfig, ConfigService, DockerRuntimeConfig, LLMConfig
from tlon.base.events.event_bus import EventBus
from tlon.base.logger import configure_logging, get_logger
from tlon.base.settings import get_tlon_home, settings
from tlon.base.tracing.trace_store import FileTraceStore
from tlon.memory.models import MemoryConfig
from tlon.organization.messaging.router import MessageRouter
from tlon.organization.messaging.store import FileMessageStore

from .api.rest import RESTServer
from .api.websocket import WebSocketServer
from .core.client_gateway import ClientGateway
from .core.notification_service import NotificationService
from .core.participant_directory import ParticipantDirectory
from .core.session_manager import SessionManager

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
        data_dir: Path | None = None,
        config_dir: Path | None = None,
    ):
        """Initialize the server application (does not create components yet).

        Args:
            host: Host to bind servers to
            rest_port: Port for REST API
            ws_port: Port for WebSocket API
            data_dir: Directory for persistence
            config_dir: Directory containing agent configs
        """
        self.host = host
        self.rest_port = rest_port
        self.ws_port = ws_port
        self.data_dir = data_dir or settings.data_dir
        self.config_dir = config_dir or (get_tlon_home() / "config")

        # Config subdirectories
        self.agents_dir = self.config_dir / "agents"

        # DI Container - all components created in initialize()
        self._components: dict[str, Any] = {}

        # Subsystem references (for convenient access)
        self.client_gateway: ClientGateway | None = None
        self.rest: RESTServer | None = None
        self.websocket: WebSocketServer | None = None

        # State
        self._shutdown_event = asyncio.Event()
        self._running = False

    async def initialize(self) -> None:
        """Initialize all components with full Dependency Injection.

        This is the DI assembly phase where all components are created
        and wired together. No network ports are opened yet.
        """
        logger.info("Initializing Tlon Server with full DI...")

        # ========== Step 1: Core Infrastructure ==========
        # 1.1 EventBus - event bus for inter-component communication (create first)
        event_bus = EventBus()
        await event_bus.initialize()
        self._components["event_bus"] = event_bus
        logger.info("EventBus initialized")

        # 1.2 ConfigService - configuration management (needs EventBus)
        config_service = ConfigService(
            event_bus=event_bus,
            config_dir=self.agents_dir.parent,
            data_dir=self.data_dir,
        )
        await config_service.initialize()
        self._components["config_service"] = config_service

        # ========== Step 2: Other Core Components ==========

        # 2.2 MessageStore - storage for messages (stateful)
        message_store = FileMessageStore(
            data_dir=self.data_dir / "chat_history", filename="messages.jsonl"
        )
        await message_store.initialize()
        self._components["message_store"] = message_store
        logger.info("MessageStore initialized")

        # 2.2b TraceStore - storage for traces (stateful)
        trace_store = FileTraceStore(data_dir=self.data_dir / "traces")
        self._components["trace_store"] = trace_store
        logger.info("TraceStore initialized")

        # 2.3 MessageRouter - stateless message routing
        # Depends on EventBus, MessageStore, and ConfigService (as AgentInfoProvider)
        message_router = MessageRouter(
            event_bus=event_bus,
            message_store=message_store,
            agent_info_provider=config_service,  # ConfigService implements AgentInfoProvider
        )
        await message_router.initialize()
        self._components["message_router"] = message_router
        logger.info("MessageRouter initialized")

        # 2.4 AgentRegistry - agent management
        # Depends on EventBus, ConfigService (as AgentInfoProvider), MessageStore
        agent_registry = AgentRegistry(
            event_bus=event_bus,
            agent_info_provider=config_service,
            message_store=message_store,
        )
        # Register initial agents (by ID, config is looked up from provider)
        for agent_id in config_service.agents:
            agent_registry.register(agent_id)

        await agent_registry.initialize()
        self._components["agent_registry"] = agent_registry
        logger.info(f"Loaded {len(agent_registry.get_all())} agents")

        # ========== Step 3: Client Gateway Components ==========
        # 3.1 Session Manager - session lifecycle management
        session_manager = SessionManager()
        self._components["session_manager"] = session_manager
        logger.info("SessionManager created")

        # 3.2 Notification Service - event pushing to clients
        notification_service = NotificationService(
            event_bus=event_bus,
            session_manager=session_manager,
        )
        self._components["notification_service"] = notification_service
        logger.info("NotificationService created")

        # 3.3 Participant Directory - participant aggregation
        participant_directory = ParticipantDirectory(
            agent_registry=agent_registry,
            session_manager=session_manager,
        )
        self._components["participant_directory"] = participant_directory
        logger.info("ParticipantDirectory created")

        # 3.4 Client Gateway - facade coordinating all components
        self.client_gateway = ClientGateway(
            session_manager=session_manager,
            notification_service=notification_service,
            participant_directory=participant_directory,
            event_bus=event_bus,
            message_store=message_store,
            config_manager=config_service,
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
            config_manager=config_service,  # For /admin endpoints
            trace_store=trace_store,  # For /traces endpoints
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
        logger.info(f"  Agents: {len(agent_registry.get_all())}")
        logger.info(f"  REST API: http://{self.host}:{self.rest_port}")
        logger.info(f"  WebSocket: ws://{self.host}:{self.ws_port}/ws")

    def _get_initial_agents(self, config_service: ConfigService) -> list[AgentConfig]:
        """Get initial agent configurations.

        Returns agents from config files, or defaults if none exist.
        """
        agents = config_service.agents
        if agents:
            logger.info(f"Loaded {len(agents)} agents from config")
            return list(agents.values())

        logger.warning("No agent configs found, using defaults")
        return self._create_default_configs()

    def _create_default_configs(self) -> list[AgentConfig]:
        """Create default agent configurations."""
        logger.info("Creating default agent configuration")

        return [
            AgentConfig(
                id="dev-001",
                name="林知遥",
                identity="你是一名技术能力强的开发工程师，注重细节，喜欢钻研。",
                profile="开发工程师。负责代码编写、Bug 修复和技术方案实现。",
                behavior="收到@时响应。被@时先分析问题，再规划方案，调用工具验证，最后给出回复。",
                llm=LLMConfig(
                    base_url=settings.llm_base_url,
                    auth_token=settings.llm_api_key,
                    model_name=settings.llm_model_name,
                    temperature=settings.llm_temperature,
                ),
                docker=DockerRuntimeConfig(
                    image=settings.docker_image,
                    memory_limit=settings.docker_memory_limit,
                ),
                memory=MemoryConfig(agent_id="dev-001"),
                proactive=False,
            )
        ]

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
            await message_router.shutdown()

        event_bus = self._components.get("event_bus")
        if event_bus:
            await event_bus.shutdown()

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
