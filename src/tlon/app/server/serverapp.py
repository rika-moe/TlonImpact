"""Tlon Server Application with full Dependency Injection.

Main entry point for the server process. The runtime (bus, registry,
delivery, adam, ...) is assembled by ``tlon.api.build_runtime()`` (ADR
0019); ServerApp assembles only its own serving side — session /
notification / client gateway / HTTP lifecycle — on top of the runtime's
domain facades.

Usage:
    >>> from tlon.app.server.serverapp import ServerApp
    >>> app = ServerApp()
    >>> await app.initialize()  # runtime + serving assembly
    >>> await app.start()       # Start servers, block until shutdown
    >>> await app.shutdown()    # Graceful shutdown
"""

import asyncio
import functools
import signal
from pathlib import Path

from tlon.api.bootstrap import Runtime, build_runtime
from tlon.base.events.event import Event, EventType
from tlon.base.logger import configure_logging, get_logger
from tlon.base.paths import paths
from tlon.base.settings import get_settings

from .api.rest import WEBUI_DIST_DIR, RESTServer
from .core.client_gateway import ClientGateway
from .core.member_directory import MemberDirectory
from .core.notification_service import NotificationService
from .core.session_manager import SessionManager

logger = get_logger(__name__)

# Hosts that keep the server reachable only from this machine. Anything
# else (wildcard or a LAN/public address) exposes an unauthenticated API.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _startup_banner(host: str, rest_port: int) -> str:
    """User-facing startup banner printed to the terminal by `tlon dream`."""
    base = f"http://{host}:{rest_port}"
    lines = [
        "",
        "🐙 Tlon dream is running",
        f"   REST + SSE : {base}",
    ]
    if (WEBUI_DIST_DIR / "index.html").is_file():
        lines.append(f"   WebUI      : {base}/ui/")
    else:
        lines.append(
            "   WebUI      : not built (cd apps/webui && npm install && npm run build)"
        )
    if host not in _LOOPBACK_HOSTS:
        lines.append(
            "   ⚠️  Security  : non-loopback bind — Tlon has NO authentication;"
        )
        lines.append(
            "                 anyone who can reach this port can read mail"
        )
        lines.append(
            "                 and call admin endpoints"
        )
    lines.append("")
    return "\n".join(lines)


class ServerApp:
    """Server application with full Dependency Injection.

    ServerApp is responsible for:
    - Bootstrapping the runtime via ``build_runtime()`` (ADR 0019)
    - Assembling the serving side (session/notification/gateway/REST)
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
        self.shared_dir = paths.shared_dir()

        # The assembled runtime (set by initialize())
        self._runtime: Runtime | None = None

        # Subsystem references (for convenient access)
        self.client_gateway: ClientGateway | None = None
        self.rest: RESTServer | None = None

        # State
        self._shutdown_event = asyncio.Event()
        self._running = False

    async def initialize(self) -> None:
        """Initialize the runtime and the serving side with full DI.

        The runtime assembly (bus/config/registry/delivery/adam/...) lives
        in ``build_runtime()`` (tlon.api.bootstrap); this method assembles
        the serving side on top of the runtime's domain facades. No
        network ports are opened yet.
        """
        logger.info("server_initializing")

        runtime = await build_runtime()
        self._runtime = runtime

        # ========== Client Gateway Components (serving side) ==========
        # Session Manager - session lifecycle management
        session_manager = SessionManager()
        logger.info("component_initialized", component="session_manager")

        # Notification Service - event pushing to clients (subscribes to
        # runtime events through the events facade, not the bus directly)
        notification_service = NotificationService(
            events=runtime.events,
            session_manager=session_manager,
        )
        logger.info("component_initialized", component="notification_service")

        # Member Directory - member aggregation (address book)
        member_directory = MemberDirectory(
            members=runtime.members,
            events=runtime.events,
        )
        logger.info("component_initialized", component="member_directory")

        # Client Gateway - facade coordinating all components
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

        # ========== API Layer (DI) ==========
        settings = get_settings()
        self.rest = RESTServer(
            self.client_gateway,
            members=runtime.members,
            mail=runtime.mail,
            prototypes=runtime.prototypes,
            observability=runtime.observability,
            configs=runtime.configs,
            events=runtime.events,
            host=self.host,
            port=self.rest_port,
            admin_token=settings.admin_api_token or None,  # ADR 0013
            github_webhook_secret=settings.github_webhook_secret or None,
        )

        self._running = False
        agent_count = len(runtime.members.list_ids())
        await runtime.event_bus.publish(Event(
            type=EventType.SYSTEM_STARTUP,
            payload={"agents": agent_count},
            source="server",
        ))
        logger.info(
            "server_initialized",
            agent_count=agent_count,
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

        agent_count = (
            len(self._runtime.members.list_ids()) if self._runtime else 0
        )
        logger.info(
            "server_started",
            host=self.host,
            rest_port=self.rest_port,
            agent_count=agent_count,
        )
        if self.host not in _LOOPBACK_HOSTS:
            logger.warning(
                "server_exposed_non_loopback",
                host=self.host,
                rest_port=self.rest_port,
            )
        if get_settings().admin_api_token:
            logger.info("admin_auth_enabled")

        print(_startup_banner(self.host, self.rest_port), flush=True)

        # Wait for shutdown signal
        await self._shutdown_event.wait()

        logger.info("shutdown_signal_received")

    async def shutdown(self) -> None:
        """Shutdown the server gracefully.

        Stops API servers and shuts down all components in reverse order.
        """
        if not self._running and self._runtime is None:
            return

        runtime = self._runtime
        if runtime:
            await runtime.event_bus.publish(Event(
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

        # Shutdown the runtime components (reverse order of creation;
        # Adam (PID 1) stops before the registry (kernel) it drives)
        if runtime:
            await runtime.shutdown()

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
