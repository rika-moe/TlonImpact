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
import functools
import signal
from pathlib import Path
from typing import Any

from tlon.agents.adam import make_adam
from tlon.agents.agent_registry import AgentRegistry
from tlon.agents.bootstrap import bootstrap_standard_team
from tlon.agents.prototypes import seed_prototypes
from tlon.base.config import ConfigService
from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus
from tlon.base.logger import configure_logging, get_logger
from tlon.base.metrics import MetricsService, init_metrics, shutdown_metrics
from tlon.base.paths import paths
from tlon.base.settings import get_settings, get_tlon_home
from tlon.base.tracing.otel import init_tracing, shutdown_tracing
from tlon.base.tracing.trace_store import FileTraceStore
from tlon.organization.mail.delivery import MailDelivery
from tlon.organization.messaging.human_adapter import AdapterManager

from .api.rest import RESTServer
from .core.client_gateway import ClientGateway
from .core.member_directory import MemberDirectory
from .core.notification_service import NotificationService
from .core.session_manager import SessionManager

logger = get_logger(__name__)


class ServerApp:
    """Server application with full Dependency Injection.

    ServerApp is responsible for:
    - Creating all core components (DI container)
    - Managing component lifecycle (initialize → start → shutdown)
    - Coordinating graceful shutdown

    Example:
        >>> app = ServerApp(host="0.0.0.0", rest_port=8080)
        >>> await app.initialize()  # DI assembly
        >>> await app.start()       # Start serving
        >>> await app.shutdown()    # Cleanup
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        rest_port: int = 8080,
        data_dir: Path | None = None,
        config_dir: Path | None = None,
    ):
        """Initialize the server application (does not create components yet).

        Args:
            host: Host to bind servers to
            rest_port: Port for the HTTP API (REST + SSE)
            data_dir: Directory for persistence
            config_dir: Directory containing agent configs
        """
        self.host = host
        self.rest_port = rest_port
        self.tlon_home = get_tlon_home()
        self.shared_dir = paths.shared_dir()

        # State
        self._components: dict[str, Any] = {}

        # Subsystem references (for convenient access)
        self.client_gateway: ClientGateway | None = None
        self.rest: RESTServer | None = None

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
        # Metrics pillar: process-wide MeterProvider + in-memory reader
        init_metrics()

        # ========== Step 1: Core Infrastructure ==========
        # 1.0 Seed built-in behavior prototypes (idempotent; before any agent starts)
        seeded = seed_prototypes()
        logger.info("prototypes_ready", seeded=seeded)

        # 1.0b First-boot bootstrap (wizard removal): an empty society gets
        # the standard team from its role prototypes. Idempotent — skips
        # when any agent already exists. Must run before ConfigService
        # reads the agent configs below.
        bootstrapped = bootstrap_standard_team()
        if bootstrapped:
            logger.info(
                "server_bootstrapped_standard_team",
                agent_ids=bootstrapped,
            )

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

        # 1.3 MetricsService - metrics pillar (pure event consumer, needs EventBus)
        metrics_service = MetricsService(event_bus=event_bus)
        await metrics_service.initialize()
        self._components["metrics_service"] = metrics_service
        logger.info("component_initialized", component="metrics_service")

        # ========== Step 2: Other Core Components ==========

        # 2.2b TraceStore - storage for traces (stateful)
        trace_store = FileTraceStore(data_dir=paths.traces_dir())
        self._components["trace_store"] = trace_store
        logger.info("component_initialized", component="trace_store")

        # 2.4 AgentRegistry - agent management
        # Depends on EventBus and ConfigService (as AgentInfoProvider)
        agent_registry = AgentRegistry(
            event_bus=event_bus,
            agent_info_provider=config_service,
        )
        # Register initial agents (by ID, config is looked up from provider)
        for agent_id in config_service.agents:
            agent_registry.register(agent_id)

        await agent_registry.initialize()
        self._components["agent_registry"] = agent_registry
        logger.info("agents_loaded", agent_count=len(agent_registry.get_all_ids()))

        # 2.4b Adam — built-in lifecycle policy member (PID 1; members.md 阶段 D).
        # Kernel first, PID 1 second: Adam is initialized after the registry
        # and issues the boot start requests. It is a MemberRuntime assembled
        # from the built-in ADAM_SPEC (policy as data; phase C unified runtime).
        # It is a bus member, not an agent — it is not registered in the agent
        # registry or MemberDirectory.
        adam = make_adam(event_bus=event_bus, agent_registry=agent_registry)
        await adam.initialize()
        self._components["adam"] = adam
        logger.info("component_initialized", component="adam")

        # 2.5 AdapterManager - human adapter lifecycle (one per username)
        adapter_manager = AdapterManager(
            event_bus=event_bus,
            agent_info_provider=config_service,
        )
        await adapter_manager.initialize()
        self._components["adapter_manager"] = adapter_manager
        logger.info("component_initialized", component="adapter_manager")

        # 2.6 Member Directory - member aggregation (address book)
        member_directory = MemberDirectory(
            agent_registry=agent_registry,
            adapter_manager=adapter_manager,
        )
        self._components["member_directory"] = member_directory
        logger.info("component_initialized", component="member_directory")

        # 2.7 MailDelivery - mail persistence + per-recipient delivery
        # Depends on EventBus, ConfigService (agent set) and AdapterManager
        # (online humans); the router's successor (docs/design/mail.md)
        mail_delivery = MailDelivery(
            event_bus=event_bus,
            agent_info_provider=config_service,
            adapter_manager=adapter_manager,
        )
        await mail_delivery.initialize()
        self._components["mail_delivery"] = mail_delivery
        logger.info("component_initialized", component="mail_delivery")

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

        # 3.3 Client Gateway - facade coordinating all components
        self.client_gateway = ClientGateway(
            session_manager=session_manager,
            notification_service=notification_service,
            member_directory=member_directory,
            data_dir=self.shared_dir,
            host=self.host,
            port=self.rest_port,
        )
        await self.client_gateway.initialize()
        logger.info("component_initialized", component="client_gateway")

        # ========== Step 4: API Layer (DI) ==========
        self.rest = RESTServer(
            self.client_gateway,
            agent_registry=agent_registry,  # Direct access for agent operations
            config_manager=config_service,  # For /admin endpoints
            trace_store=trace_store,  # For /traces endpoints
            metrics_service=metrics_service,  # For /metrics endpoint
            logs_dir=paths.logs_dir(),  # For /logs endpoints
            paths=paths,  # For /inbox mailbox resolution
            adapter_manager=adapter_manager,  # For POST /messages
            host=self.host,
            port=self.rest_port,
        )

        self._running = False
        await event_bus.publish(Event(
            type=EventType.SYSTEM_STARTUP,
            payload={"agents": len(agent_registry.get_all_ids())},
            source="server",
        ))
        logger.info(
            "server_initialized",
            agent_count=len(agent_registry.get_all_ids()),
            url=f"http://{self.host}:{self.rest_port}",
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

        # Start API server (created in initialize(); safe to start here)
        assert self.rest is not None
        await self.rest.start()

        agent_registry = self._components.get("agent_registry")
        logger.info(
            "server_started",
            host=self.host,
            rest_port=self.rest_port,
            agent_count=len(agent_registry.get_all_ids()) if agent_registry else 0,
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

        # Stop API server
        if self.rest:
            await self.rest.stop()

        # Shutdown ClientGateway
        if self.client_gateway:
            await self.client_gateway.shutdown()

        # Shutdown core components (reverse order of creation)
        # Adam (PID 1) stops before the registry (kernel) it drives
        adam = self._components.get("adam")
        if adam:
            await adam.shutdown()

        agent_registry = self._components.get("agent_registry")
        if agent_registry:
            await agent_registry.shutdown()

        mail_delivery = self._components.get("mail_delivery")
        if mail_delivery:
            await mail_delivery.shutdown()

        metrics_service = self._components.get("metrics_service")
        if metrics_service:
            await metrics_service.shutdown()

        adapter_manager = self._components.get("adapter_manager")
        if adapter_manager:
            await adapter_manager.shutdown()

        event_bus = self._components.get("event_bus")
        if event_bus:
            await event_bus.shutdown()

        # Flush and shutdown tracing/metrics last
        shutdown_tracing()
        shutdown_metrics()

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
    log_level: str = "INFO",
    debug: bool = False,
) -> int:
    """Run the Tlon server.

    This is the main entry point for the server process.

    Args:
        host: Host to bind to
        rest_port: Port for the HTTP API (REST + SSE)
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

    logger.info("server_starting", host=host, rest_port=rest_port)

    # No-key guidance: the server runs fine, but cognitive members fail on
    # their first LLM call. Point the operator at the Settings view.
    if not settings.llm_api_key:
        logger.warning(
            "llm_api_key_not_configured",
            hint="在 ops 的 Settings 视图（数字键 7）配置 LLM 参数，"
            "或设 OPENAI_API_KEY 环境变量",
        )

    # Create and run server
    app = ServerApp(host=host, rest_port=rest_port)

    # Setup signal handlers
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, functools.partial(app.handle_signal, sig))

    try:
        await app.initialize()
        await app.start()
        return 0
    except Exception as e:
        logger.error("server_error", error=str(e), exc_info=True)
        return 1
    finally:
        await app.shutdown()
