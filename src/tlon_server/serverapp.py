"""Tlon Server Application with full Dependency Injection.

Main entry point for the server process. The runtime (bus, registry,
delivery, adam, ...) is assembled by ``tlon.api.build_runtime()`` (ADR
0019); ServerApp assembles only its own serving side — session /
notification / client gateway / HTTP lifecycle — on top of the runtime's
domain facades.

Usage:
    >>> from tlon_server.serverapp import ServerApp
    >>> app = ServerApp()
    >>> await app.initialize()  # runtime + serving assembly
    >>> await app.start()       # Start servers, block until shutdown
    >>> await app.shutdown()    # Graceful shutdown
"""

import asyncio
import functools
import signal

from tlon.api.bootstrap import Runtime, build_runtime
from tlon.base.events.event import Event, EventType
from tlon.base.logger import configure_logging, get_logger
from tlon.base.metrics import init_metrics, shutdown_metrics
from tlon.base.paths import TlonPaths
from tlon.base.settings import Settings, load_process_env
from tlon.base.tracing.otel import init_tracing, shutdown_tracing
from tlon_server.api.rest import WEBUI_DIST_DIR, RESTServer
from tlon_server.core.client_gateway import ClientGateway
from tlon_server.core.member_directory import MemberDirectory
from tlon_server.core.notification_service import NotificationService
from tlon_server.core.session_manager import SessionManager

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
        lines.append("   WebUI      : not built (cd apps/webui && npm install && npm run build)")
    if host not in _LOOPBACK_HOSTS:
        lines.append("   ⚠️  Security  : non-loopback bind — Tlon has NO authentication;")
        lines.append("                 anyone who can reach this port can read mail")
        lines.append("                 and call admin endpoints")
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
        tlon_paths: TlonPaths | None = None,
        settings: Settings | None = None,
    ):
        """Initialize the server application (does not create components yet).

        Args:
            host: Host to bind servers to
            rest_port: Port for the HTTP API (REST + SSE)
            tlon_paths: Path resolver for the runtime's tlon home; None
                resolves ``$TLON_HOME`` / ``~/.tlon`` — the process entry
                point constructs exactly one and hands it down
            settings: Process-level settings snapshot (env + secrets),
                built by the entry point (``run_server``); None builds one
                from the current environment (bare test setups)
        """
        self.host = host
        self.rest_port = rest_port
        self._paths = tlon_paths or TlonPaths()
        self._settings = settings or Settings.from_env()
        self.shared_dir = self._paths.shared_dir()

        # The assembled runtime (set by initialize())
        self._runtime: Runtime | None = None

        # Subsystem references (for convenient access)
        self.client_gateway: ClientGateway | None = None
        self.rest: RESTServer | None = None

        # State
        self._shutdown_event = asyncio.Event()
        self._running = False
        # shutdown() re-entry guard: the signal handler spawns one
        # shutdown task per signal and run_server's finally always calls
        # shutdown() again after start() returns — without this flag the
        # second pass re-publishes SYSTEM_SHUTDOWN onto an already-stopped
        # EventBus and raises. Set at entry (before the first await) so a
        # concurrent second task bails out immediately too.
        self._shutdown_started = False

    async def initialize(self) -> None:
        """Initialize the runtime and the serving side with full DI.

        The runtime assembly (bus/config/registry/delivery/adam/...) lives
        in ``build_runtime()`` (tlon.api.bootstrap); this method assembles
        the serving side on top of the runtime's domain facades. No
        network ports are opened yet.
        """
        logger.info("server_initializing")

        # Tracing / metrics pillars (process-wide, OTel providers are
        # process singletons): initialized here at the orchestration layer,
        # NOT inside build_runtime — a process may host several runtimes,
        # and one runtime's shutdown must never flush another's pillars.
        init_tracing(
            self._paths.traces_dir(),
            otlp_endpoint=self._settings.otel_exporter_otlp_endpoint,
        )
        init_metrics()

        runtime = await build_runtime(self._paths, self._settings)
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
        self.rest = RESTServer(
            self.client_gateway,
            members=runtime.members,
            mail=runtime.mail,
            prototypes=runtime.prototypes,
            observability=runtime.observability,
            configs=runtime.configs,
            events=runtime.events,
            arena=runtime.arena,
            capabilities=runtime.capabilities,
            host=self.host,
            port=self.rest_port,
            admin_token=self._settings.admin_api_token or None,  # ADR 0013
            github_webhook_secret=self._settings.github_webhook_secret or None,
        )

        self._running = False
        agent_count = len(runtime.members.list_ids())
        await runtime.event_bus.publish(
            Event(
                type=EventType.SYSTEM_STARTUP,
                payload={"agents": agent_count},
                source="server",
            )
        )
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

        agent_count = len(self._runtime.members.list_ids()) if self._runtime else 0
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
        if self._settings.admin_api_token:
            logger.info("admin_auth_enabled")

        print(_startup_banner(self.host, self.rest_port), flush=True)

        # Wait for shutdown signal
        await self._shutdown_event.wait()

        logger.info("shutdown_signal_received")

    async def shutdown(self) -> None:
        """Shutdown the server gracefully.

        Stops API servers and shuts down all components in reverse order.
        Idempotent: re-entry (a second signal's shutdown task, or
        run_server's finally after a completed shutdown) returns
        immediately — the EventBus is stopped by the first pass and
        cannot take a second SYSTEM_SHUTDOWN publish.
        """
        if self._shutdown_started:
            return
        self._shutdown_started = True

        if not self._running and self._runtime is None:
            return

        runtime = self._runtime
        if runtime:
            await runtime.event_bus.publish(
                Event(
                    type=EventType.SYSTEM_SHUTDOWN,
                    payload={},
                    source="server",
                )
            )

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

        # Process-wide pillars flush last (owned here, not by the runtime)
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
    tlon_paths: TlonPaths | None = None,
    settings: Settings | None = None,
) -> int:
    """Run the Tlon server.

    This is the main entry point for the server process.

    Args:
        host: Host to bind to
        rest_port: Port for the HTTP API (REST + SSE)
        log_level: Logging level
        debug: Enable debug mode
        tlon_paths: Path resolver from the process entry point; None
            resolves ``$TLON_HOME`` / ``~/.tlon`` (bare embedding)
        settings: Process settings snapshot from the entry point; None
            loads ``{TLON_HOME}/.env`` and snapshots the environment
            (bare embedding)

    Returns:
        Exit code (0 for success)
    """
    # Configure logging (stdout + daily JSONL file sink under ~/.tlon/shared/logs/).
    # 进程入口纪律（ADR 0019）：TlonPaths / Settings 由入口构造一次并往下
    # 传；裸嵌入（无注入）时这里兜底构造，.env 回退加载必须在任何 env
    # 读取（Settings.from_env）之前。
    tlon_paths = tlon_paths or TlonPaths()
    if settings is None:
        load_process_env(tlon_paths)
        settings = Settings.from_env()
    configure_logging(
        log_level=log_level,
        debug=debug,
        log_dir=tlon_paths.logs_dir(),
        retention_days=settings.log_retention_days,
    )

    logger.info("server_starting", host=host, rest_port=rest_port)

    # Create and run server
    app = ServerApp(host=host, rest_port=rest_port, tlon_paths=tlon_paths, settings=settings)

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
