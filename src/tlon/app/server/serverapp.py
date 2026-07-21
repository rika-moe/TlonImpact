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
from tlon.base.config import ConfigService
from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus
from tlon.base.logger import configure_logging, get_logger
from tlon.base.paths import paths
from tlon.base.settings import get_settings, get_tlon_home
from tlon.base.tracing.otel import init_tracing, shutdown_tracing
from tlon.base.tracing.trace_store import FileTraceStore
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
        self.tlon_home = get_tlon_home()
        self.shared_dir = paths.shared_dir()

        # State
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
        logger.info("server_initializing")

        # ========== Step 0: Tracing (must precede any span creation) ==========
        init_tracing(
            paths.traces_dir(),
            otlp_endpoint=get_settings().otel_exporter_otlp_endpoint,
        )

        # ========== Step 1: Core Infrastructure ==========
        # 1.1 EventBus - event bus for inter-component communication (create first)
        event_bus = EventBus()
        await event_bus.initialize()
        self._components["event_bus"] = event_bus
        logger.info("component_initialized", component="event_bus")

        # 1.2 ConfigService - configuration management (needs EventBus)
        config_service = ConfigService(
            event_bus=event_bus,
            tlon_home=self.tlon_home,
        )
        await config_service.initialize()
        self._components["config_service"] = config_service

        # ========== Step 2: Other Core Components ==========

        # 2.2 MessageStore - storage for messages (stateful)
        message_store = FileMessageStore(
            data_dir=paths.chat_history_dir(), filename="messages.jsonl"
        )
        # 2.2b TraceStore - storage for traces (stateful)
        trace_store = FileTraceStore(data_dir=paths.traces_dir())
        self._components["trace_store"] = trace_store
        logger.info("component_initialized", component="trace_store")

        # 2.3 MessageRouter - stateless message routing
        # Depends on EventBus, MessageStore, and ConfigService (as AgentInfoProvider)
        message_router = MessageRouter(
            event_bus=event_bus,
            message_store=message_store,
            agent_info_provider=config_service,  # ConfigService implements AgentInfoProvider
        )
        await message_router.initialize()
        self._components["message_router"] = message_router
        logger.info("component_initialized", component="message_router")

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
        logger.info("agents_loaded", agent_count=len(agent_registry.get_all()))

        # ========== Step 3: Client Gateway Components ==========
        # 3.1 Session Manager - session lifecycle management
        session_manager = SessionManager()
        self._components["session_manager"] = session_manager
        logger.info("component_initialized", component="session_manager")

        # 3.2 Notification Service - event pushing to clients
        notification_service = NotificationService(
            event_bus=event_bus,
            session_manager=session_manager,
        )
        self._components["notification_service"] = notification_service
        logger.info("component_initialized", component="notification_service")

        # 3.3 Participant Directory - participant aggregation
        participant_directory = ParticipantDirectory(
            agent_registry=agent_registry,
            session_manager=session_manager,
        )
        self._components["participant_directory"] = participant_directory
        logger.info("component_initialized", component="participant_directory")

        # 3.4 Client Gateway - facade coordinating all components
        self.client_gateway = ClientGateway(
            session_manager=session_manager,
            notification_service=notification_service,
            participant_directory=participant_directory,
            event_bus=event_bus,
            message_store=message_store,
            config_manager=config_service,
            data_dir=self.shared_dir,
            host=self.host,
            port=self.ws_port,
        )
        await self.client_gateway.initialize()
        logger.info("component_initialized", component="client_gateway")

        # ========== Step 4: API Layer (DI) ==========
        self.rest = RESTServer(
            self.client_gateway,
            agent_registry=agent_registry,  # Direct access for agent operations
            config_manager=config_service,  # For /admin endpoints
            trace_store=trace_store,  # For /traces endpoints
            logs_dir=paths.logs_dir(),  # For /logs endpoints
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
        await event_bus.publish(Event(
            type=EventType.SYSTEM_STARTUP,
            payload={"agents": len(agent_registry.get_all())},
            source="server",
        ))
        logger.info(
            "server_initialized",
            agent_count=len(agent_registry.get_all()),
            rest_url=f"http://{self.host}:{self.rest_port}",
            ws_url=f"ws://{self.host}:{self.ws_port}/ws",
        )

    # ========== Lifecycle Methods ==========

    async def start(self) -> None:
        """Start the server and block until shutdown.

        This starts all API servers and waits for a shutdown signal.
        """
        if self._running:
            logger.warning("server_already_running")
            return

        self._running = True

        # Start API servers
        await self.rest.start()
        await self.websocket.start()

        agent_registry = self._components.get("agent_registry")
        logger.info(
            "server_started",
            host=self.host,
            rest_port=self.rest_port,
            ws_port=self.ws_port,
            agent_count=len(agent_registry.get_all()) if agent_registry else 0,
        )

        # Wait for shutdown signal
        await self._shutdown_event.wait()

        logger.info("shutdown_signal_received")

    async def shutdown(self) -> None:
        """Shutdown the server gracefully.

        Stops API servers and shuts down all components in reverse order.
        """
        if not self._running and not self._components:
            return

        event_bus = self._components.get("event_bus")
        if event_bus:
            await event_bus.publish(Event(
                type=EventType.SYSTEM_SHUTDOWN,
                payload={},
                source="server",
            ))

        logger.info("server_stopping")

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

        # Flush and shutdown tracing last
        shutdown_tracing()

        self._running = False
        self._shutdown_event.set()

        logger.info("server_stopped")

    def handle_signal(self, sig: signal.Signals) -> None:
        """Handle shutdown signals.

        Args:
            sig: The signal that was received
        """
        logger.info("signal_received", signal=sig.name)
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
    # Configure logging (stdout + daily JSONL file sink under ~/.tlon/shared/logs/)
    settings = get_settings()
    configure_logging(
        log_level=log_level,
        debug=debug,
        log_dir=paths.logs_dir(),
        retention_days=settings.log_retention_days,
    )

    logger.info("server_starting", host=host, rest_port=rest_port, ws_port=ws_port)

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
        logger.error("server_error", error=str(e), exc_info=True)
        return 1
    finally:
        await app.shutdown()
