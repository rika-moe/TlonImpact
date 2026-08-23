"""REST API for Tlon Server — application assembly and server lifecycle.

``RESTAPIHandler`` aggregates the per-domain handler mixins (mail,
sessions, admin agents, admin prototypes, settings, observability),
owns the constructor injection and the ``create_app`` route table, and
hosts the core misc endpoints (health/config, agents listing, WebUI
static hosting, debug test endpoints). ``RESTServer`` wraps the aiohttp
runner lifecycle.

Provides HTTP endpoints for:
- Configuration queries
- Mail inbox retrieval
- Agent management
- Session information
- Client sessions + SSE event stream (protocol v2)
"""

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from aiohttp import web

from tlon.agents.agent_registry import AgentRegistry
from tlon.app.server.core.client_gateway import ClientGateway
from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths
from tlon.organization.messaging.human_adapter import AdapterManager

from .admin_agents import AdminAgentHandlers
from .admin_prototypes import AdminPrototypeHandlers
from .mail import MailHandlers
from .observability import ObservabilityHandlers
from .sessions import SessionHandlers
from .settings import SettingsHandlers

# Keep the pre-split logger name so log files keep a stable "logger" field.
logger = get_logger("tlon.app.server.api.rest")

# WebUI static hosting: built assets of apps/webui (npm run build).
# Repository root: src/tlon/app/server/api/rest/app.py -> parents[6]
_REPO_ROOT = Path(__file__).resolve().parents[6]
WEBUI_DIST_DIR = _REPO_ROOT / "apps" / "webui" / "dist"


class RESTAPIHandler(
    MailHandlers,
    SessionHandlers,
    AdminAgentHandlers,
    AdminPrototypeHandlers,
    SettingsHandlers,
    ObservabilityHandlers,
):
    """Handler for REST API requests.

    Routes:
    - GET /health - Health check
    - GET /config - Server configuration
    - GET /inbox - Member inbox (mail model)
    - POST /messages - Send a mail as a human member (protocol v2, PR-1)
    - POST /sessions - Establish a client session (protocol v2, PR-2)
    - PATCH /sessions/{id} - Update session mode/filters (protocol v2, PR-2)
    - DELETE /sessions/{id} - Disconnect a session (protocol v2, PR-2)
    - GET /events - SSE event stream for a session (protocol v2, PR-2)
    - GET /agents - List all agents
    - GET /agents/{id} - Get a single agent
    - GET /sessions - List active sessions
    - GET /participants - List connected members
    - GET /traces - List traces
    - GET /traces/{trace_id} - Get a single trace
    - GET /agents/{id}/logs - Agent log entries
    - GET /logs - All log entries with combined filters
    - GET /metrics - In-process metrics snapshot

    Example:
        >>> handler = RESTAPIHandler(client_gateway, agent_registry)
        >>> app = handler.create_app()
        >>> runner = web.AppRunner(app)
        >>> await runner.setup()
        >>> site = web.TCPSite(runner, 'localhost', 8080)
        >>> await site.start()
    """

    def __init__(
        self,
        client_gateway: ClientGateway,
        agent_registry: AgentRegistry,
        config_manager: Any | None = None,
        trace_store: Any | None = None,
        metrics_service: Any | None = None,
        logs_dir: Path | None = None,
        paths: TlonPaths | None = None,
        adapter_manager: AdapterManager | None = None,
        debug: bool = False,
    ):
        """Initialize the REST API handler.

        Args:
            client_gateway: The ClientGateway instance
            agent_registry: The AgentRegistry instance for agent operations
            config_manager: Optional ConfigManager for hot-reload support
            trace_store: Optional TraceStore for trace operations
            metrics_service: Optional MetricsService for the /metrics snapshot
            logs_dir: Optional directory with server-{date}.jsonl log files
            paths: Optional TlonPaths for mailbox resolution (/inbox)
            adapter_manager: Optional AdapterManager for sending mail (/messages)
            debug: Whether to enable debug/test endpoints (never enable in production)
        """
        self.client_gateway = client_gateway
        self.agent_registry = agent_registry
        self.config_manager = config_manager
        self.debug = debug
        self.trace_store = trace_store
        self.metrics_service = metrics_service
        self.logs_dir = logs_dir
        self.paths = paths
        self.adapter_manager = adapter_manager
        # Active SSE streams: session_id -> frame queue (None sentinel ends
        # the stream; see handle_events / handle_delete_session)
        self._sse_queues: dict[str, asyncio.Queue[str | None]] = {}

    def create_app(self) -> web.Application:
        """Create and configure the aiohttp application.

        Returns:
            Configured aiohttp Application
        """
        app = web.Application()

        # Add API routes first
        app.router.add_get("/health", self.handle_health)
        app.router.add_get("/config", self.handle_config)
        app.router.add_get("/inbox", self.handle_inbox)
        app.router.add_post("/messages", self.handle_send_message)
        app.router.add_get("/agents", self.handle_list_agents)
        app.router.add_get("/agents/{agent_id}", self.handle_get_agent)
        # Wake/stop endpoints removed - all agents start at system startup
        app.router.add_get("/sessions", self.handle_list_sessions)
        app.router.add_post("/sessions", self.handle_create_session)
        app.router.add_patch("/sessions/{session_id}", self.handle_update_session)
        app.router.add_delete("/sessions/{session_id}", self.handle_delete_session)
        app.router.add_get("/events", self.handle_events)
        app.router.add_get("/participants", self.handle_list_participants)

        # Admin routes for hot-reload (only if config_manager is available)
        if self.config_manager:
            app.router.add_get("/admin/config/agents", self.handle_list_agent_configs)
            app.router.add_post(
                "/admin/config/agents/{agent_id}/reload",
                self.handle_reload_agent,
            )

        # Admin routes for agent CRUD + prototype approval (wizard removal,
        # A2): creation/editing/removal of members and staging approval.
        app.router.add_post("/admin/config/agents", self.handle_create_agent)
        app.router.add_patch("/admin/config/agents/{agent_id}", self.handle_update_agent)
        app.router.add_delete("/admin/config/agents/{agent_id}", self.handle_delete_agent)
        app.router.add_get("/admin/prototypes/staging", self.handle_list_staging)
        app.router.add_get(
            "/admin/prototypes/staging/{name}", self.handle_get_staging_prototype
        )
        app.router.add_patch(
            "/admin/prototypes/staging/{name}", self.handle_update_staging_prototype
        )
        app.router.add_get("/admin/prototypes", self.handle_list_prototypes)
        app.router.add_post("/admin/prototypes", self.handle_create_prototype)
        app.router.add_post(
            "/admin/prototypes/staging/{name}/approve", self.handle_approve_prototype
        )
        app.router.add_post(
            "/admin/prototypes/staging/{name}/reject", self.handle_reject_prototype
        )
        app.router.add_get("/admin/prototypes/{name}", self.handle_get_prototype)
        app.router.add_patch("/admin/prototypes/{name}", self.handle_update_prototype)
        app.router.add_delete("/admin/prototypes/{name}", self.handle_delete_prototype)

        # Admin env settings (wizard removal F): read/write {TLON_HOME}/.env
        app.router.add_get("/admin/settings/env", self.handle_get_settings_env)
        app.router.add_put("/admin/settings/env", self.handle_update_settings_env)

        # Trace routes (only if trace_store is available)
        if self.trace_store:
            app.router.add_get("/traces", self.handle_list_traces)
            app.router.add_get("/traces/{trace_id}", self.handle_get_trace)

        # Metrics route (503 when the metrics service is unavailable)
        app.router.add_get("/metrics", self.handle_metrics)

        # Agent logs route
        app.router.add_get("/agents/{agent_id}/logs", self.handle_get_agent_logs)
        # Global logs route (trace -> logs stitching)
        app.router.add_get("/logs", self.handle_list_logs)

        # Test endpoints (only in debug mode — never enable in production)
        if self.debug:
            app.router.add_get("/test/ping", self.handle_test_ping)
            app.router.add_post("/test/echo", self.handle_test_echo)

        # WebUI static hosting (apps/webui/dist). Conditional registration:
        # a missing build means no route, same policy as admin/traces routes.
        if (WEBUI_DIST_DIR / "index.html").is_file():
            app.router.add_get("/ui", self.handle_webui_redirect)
            # aiohttp's add_static does not serve index.html for the prefix
            # root itself (directory requests are 403) — register it first.
            app.router.add_get("/ui/", self.handle_webui_index)
            app.router.add_static("/ui/", WEBUI_DIST_DIR, name="webui")

        # CORS
        app.router.add_options("/{path:.*}", self.handle_options)

        return app

    async def handle_webui_redirect(self, request: web.Request) -> web.Response:
        """Redirect /ui to /ui/ so the static index resolves relative assets."""
        raise web.HTTPFound("/ui/")

    async def handle_webui_index(self, request: web.Request) -> web.StreamResponse:
        """Serve the WebUI entry page."""
        return web.FileResponse(WEBUI_DIST_DIR / "index.html")

    async def handle_health(self, request: web.Request) -> web.Response:
        """GET /health - Health check endpoint.

        Returns:
            200 OK with server status
        """
        return self._json_response(
            {
                "status": "healthy",
                "running": self.client_gateway.is_running(),
                "timestamp": datetime.now().isoformat(),
                "version": "0.2.0",
            }
        )

    async def handle_config(self, request: web.Request) -> web.Response:
        """GET /config - Get server configuration.

        Returns:
            Server configuration (sanitized)
        """
        # Return non-sensitive configuration
        config = {
            "server": {
                "host": self.client_gateway.host,
                "port": self.client_gateway.port,
            },
            "agents": {
                "count": len(self.agent_registry.get_all_agent_status()),
            },
            "sessions": {
                "active": len(self.client_gateway.get_all_sessions()),
            },
        }
        return self._json_response(config)

    async def handle_list_agents(self, request: web.Request) -> web.Response:
        """GET /agents - List all agents.

        Returns:
            List of agent statuses
        """
        agents = self.agent_registry.get_all_agent_status()
        return self._json_response(
            {
                "agents": agents,
                "count": len(agents),
            }
        )

    async def handle_get_agent(self, request: web.Request) -> web.Response:
        """GET /agents/{agent_id} - Get a specific agent's status.

        Returns:
            Agent status or 404 if not found
        """
        agent_id = request.match_info["agent_id"]

        config = self.agent_registry.get_config(agent_id)
        if config is None:
            return self._json_response({"error": f"Agent '{agent_id}' not found"}, status=404)

        agent = self.agent_registry.get_agent(agent_id)
        state = agent.get_state() if agent else "unknown"

        status = {
            "id": agent_id,
            "name": config.name,
            "state": state,
            "config": {
                "prototype": config.prototype,
            },
        }
        return self._json_response(status)

    async def handle_test_ping(self, request: web.Request) -> web.Response:
        """GET /test/ping - Simple ping endpoint for testing."""
        return self._json_response(
            {
                "status": "ok",
                "pong": True,
                "timestamp": datetime.now().isoformat(),
            }
        )

    async def handle_test_echo(self, request: web.Request) -> web.Response:
        """POST /test/echo - Echo back the request body."""
        try:
            body = await request.json()
            return self._json_response(
                {
                    "status": "ok",
                    "echo": body,
                    "timestamp": datetime.now().isoformat(),
                }
            )
        except json.JSONDecodeError:
            return self._json_response({"error": "Invalid JSON"}, status=400)


class RESTServer:
    """REST API server for Tlon.

    Wraps the aiohttp server with lifecycle management.

    Example:
        >>> rest = RESTServer(client_gateway, agent_registry, config_manager, port=8080)
        >>> await rest.start()
        >>> # Server is running
        >>> await rest.stop()
    """

    def __init__(
        self,
        client_gateway: ClientGateway,
        agent_registry: AgentRegistry,
        config_manager: Any | None = None,
        trace_store: Any | None = None,
        metrics_service: Any | None = None,
        logs_dir: Path | None = None,
        paths: TlonPaths | None = None,
        adapter_manager: AdapterManager | None = None,
        host: str = "127.0.0.1",
        port: int = 8080,
        debug: bool = False,
    ):
        """Initialize the REST server.

        Args:
            client_gateway: The ClientGateway instance
            agent_registry: The AgentRegistry instance for agent operations
            config_manager: Optional ConfigManager for hot-reload support
            trace_store: Optional TraceStore for trace operations
            metrics_service: Optional MetricsService for the /metrics endpoint
            logs_dir: Optional directory with server-{date}.jsonl log files
            paths: Optional TlonPaths for mailbox resolution (/inbox)
            adapter_manager: Optional AdapterManager for sending mail (/messages)
            host: Host to bind to
            port: Port to listen on
            debug: Whether to enable debug/test endpoints (never enable in production)
        """
        self.client_gateway = client_gateway
        self.host = host
        self.port = port
        self._handler = RESTAPIHandler(
            client_gateway,
            agent_registry,
            config_manager,
            trace_store,
            metrics_service,
            logs_dir,
            paths,
            adapter_manager,
            debug,
        )
        self._app: web.Application | None = None
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._running = False

    async def start(self) -> None:
        """Start the REST server."""
        if self._running:
            logger.warning("rest_server_already_running")
            return

        self._app = self._handler.create_app()
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()

        self._site = web.TCPSite(self._runner, self.host, self.port)
        await self._site.start()

        self._running = True
        logger.info("rest_api_server_started", host=self.host, port=self.port)

    async def stop(self) -> None:
        """Stop the REST server."""
        if not self._running:
            return

        logger.info("rest_api_server_stop")

        if self._runner:
            await self._runner.cleanup()

        self._running = False
        logger.info("rest_api_server_stopped")

    def is_running(self) -> bool:
        """Check if the REST server is running.

        Returns:
            True if running
        """
        return self._running
