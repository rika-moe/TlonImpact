"""REST API for Tlon Server — application assembly and server lifecycle.

``RESTAPIHandler`` aggregates the per-domain handler mixins (mail,
sessions, admin agents, admin prototypes, admin llm, observability),
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
import hmac
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

from aiohttp import web

from tlon.api.arena import ArenaFacade
from tlon.api.capabilities import CapabilitiesFacade
from tlon.api.config import ConfigFacade
from tlon.api.events import EventsFacade
from tlon.api.mail import MailFacade
from tlon.api.members import MembersFacade
from tlon.api.observability import ObservabilityFacade
from tlon.api.prototypes import PrototypesFacade
from tlon.base.logger import get_logger
from tlon.base.version import VERSION
from tlon_server.core.client_gateway import ClientGateway

from .admin_agents import AdminAgentHandlers
from .admin_arena import AdminArenaHandlers
from .admin_capabilities import AdminCapabilityHandlers
from .admin_llm import AdminLlmHandlers
from .admin_prototypes import AdminPrototypeHandlers
from .github import GitHubWebhookMixin
from .mail import MailHandlers
from .observability import ObservabilityHandlers
from .sessions import SessionHandlers

logger = get_logger("tlon_server.api.rest")

# WebUI static hosting: built assets of apps/webui (npm run build).
# Installed layout: bundled as package data at tlon_server/webui_dist/
# (scripts/build_package.sh / verify_package.sh sync it in before uv build).
# Dev layout: apps/webui/dist in the repo checkout
# (src/tlon_server/api/rest/app.py -> parents[4]).
_PACKAGED_DIST = Path(__file__).resolve().parents[2] / "webui_dist"
_REPO_DIST = Path(__file__).resolve().parents[4] / "apps" / "webui" / "dist"
WEBUI_DIST_DIR = _PACKAGED_DIST if (_PACKAGED_DIST / "index.html").is_file() else _REPO_DIST


class RESTAPIHandler(
    MailHandlers,
    SessionHandlers,
    AdminAgentHandlers,
    AdminLlmHandlers,
    AdminPrototypeHandlers,
    AdminArenaHandlers,
    AdminCapabilityHandlers,
    ObservabilityHandlers,
    GitHubWebhookMixin,
):
    """Handler for REST API requests.

    Routes:
    - GET /health - Health check
    - GET /config - Server configuration
    - GET /inbox - Member inbox (mail model)
    - GET /members/{member_id}/mail - Any member's mailbox, read-only observer view
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
    - POST /webhooks/github - GitHub webhook ingress (only when configured)

    Example:
        >>> handler = RESTAPIHandler(client_gateway, members=runtime.members, ...)
        >>> app = handler.create_app()
        >>> runner = web.AppRunner(app)
        >>> await runner.setup()
        >>> site = web.TCPSite(runner, 'localhost', 8080)
        >>> await site.start()
    """

    def __init__(
        self,
        client_gateway: ClientGateway,
        *,
        members: MembersFacade | None = None,
        mail: MailFacade | None = None,
        prototypes: PrototypesFacade | None = None,
        observability: ObservabilityFacade | None = None,
        configs: ConfigFacade | None = None,
        events: EventsFacade | None = None,
        arena: ArenaFacade | None = None,
        capabilities: CapabilitiesFacade | None = None,
        debug: bool = False,
        admin_token: str | None = None,
        github_webhook_secret: str | None = None,
    ):
        """Initialize the REST API handler.

        Args:
            client_gateway: The ClientGateway instance
            members: Members facade (agent status/config, member CRUD)
            mail: Mail facade (GET /inbox, GET /members/{member_id}/mail, POST /messages)
            prototypes: Prototypes facade (/admin/prototypes/*)
            observability: Observability facade (/traces, /metrics, logs)
            configs: Config facade (/admin/config/agents listing, agent.yaml)
            events: Events facade (SSE presence attach/detach, GitHub ingress)
            arena: Arena facade (/admin/arena-runs, staging 演武触发/报告)
            capabilities: Capabilities facade (/admin/capabilities/*)
            debug: Whether to enable debug/test endpoints (never enable in production)
            admin_token: Shared Bearer token guarding /admin/* (ADR 0013);
                None disables authentication (default, localhost trust boundary)
            github_webhook_secret: HMAC secret of the GitHub webhook; the
                /webhooks/github route registers only when the runtime
                ingress is also enabled (events facade with adapter)
        """
        self.client_gateway = client_gateway
        self.members = members
        self.mail = mail
        self.prototypes = prototypes
        self.observability = observability
        self.configs = configs
        self.events = events
        self.arena = arena
        self.capabilities = capabilities
        self.debug = debug
        self.admin_token = admin_token
        self.github_ingress = events is not None and events.github_enabled
        self.github_webhook_secret = github_webhook_secret
        # Active SSE streams: session_id -> frame queue (None sentinel ends
        # the stream; see handle_events / handle_delete_session /
        # close_sse_streams)
        self._sse_queues: dict[str, asyncio.Queue[str | None]] = {}

    def create_app(self) -> web.Application:
        """Create and configure the aiohttp application.

        Returns:
            Configured aiohttp Application
        """
        if self.admin_token:
            app = web.Application(middlewares=[self._admin_auth_middleware])
        else:
            app = web.Application()

        # Add API routes first
        app.router.add_get("/health", self.handle_health)
        app.router.add_get("/config", self.handle_config)
        app.router.add_get("/inbox", self.handle_inbox)
        app.router.add_get("/members/{member_id}/mail", self.handle_member_mail)
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

        # Admin routes for hot-reload (only if the configs facade is wired)
        if self.configs:
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
        # Admin LLM registry (ADR 0016 §4): model metadata for the WebUI dropdown
        app.router.add_get("/admin/llm/models", self.handle_list_llm_models)
        app.router.add_get("/admin/prototypes/staging", self.handle_list_staging)
        app.router.add_get("/admin/prototypes/staging/{name}", self.handle_get_staging_prototype)
        app.router.add_patch(
            "/admin/prototypes/staging/{name}", self.handle_update_staging_prototype
        )
        app.router.add_get("/admin/prototypes", self.handle_list_prototypes)
        app.router.add_post("/admin/prototypes", self.handle_create_prototype)
        app.router.add_post(
            "/admin/prototypes/staging/{name}/approve", self.handle_approve_prototype
        )
        app.router.add_post("/admin/prototypes/staging/{name}/reject", self.handle_reject_prototype)
        app.router.add_get("/admin/prototypes/{name}", self.handle_get_prototype)
        app.router.add_patch("/admin/prototypes/{name}", self.handle_update_prototype)
        app.router.add_delete("/admin/prototypes/{name}", self.handle_delete_prototype)

        # Admin arena routes (docs/design/arena.md): human-only trigger +
        # status polling + archived report. Registered unconditionally like
        # the prototype routes; handlers 503 when the facade is not wired.
        app.router.add_post(
            "/admin/prototypes/staging/{name}/arena-runs", self.handle_start_arena_run
        )
        app.router.add_get("/admin/arena-runs", self.handle_list_arena_runs)
        app.router.add_get("/admin/arena-runs/{run_id}", self.handle_get_arena_run)
        app.router.add_get(
            "/admin/prototypes/staging/{name}/arena-runs/{run_id}/report",
            self.handle_get_arena_report,
        )

        # Admin capability routes (docs/design/capabilities.md): trusted
        # library listing/detail + the staging channel (create/list/detail/
        # approve/reject). Registered unconditionally like the prototype
        # routes; handlers 503 when the facade is not wired. The literal
        # ``staging`` routes precede the ``{name}`` ones (aiohttp matches
        # in registration order).
        app.router.add_get("/admin/capabilities/staging", self.handle_list_staging_capabilities)
        app.router.add_post("/admin/capabilities/staging", self.handle_create_staging_capability)
        app.router.add_get("/admin/capabilities/staging/{name}", self.handle_get_staging_capability)
        app.router.add_post(
            "/admin/capabilities/staging/{name}/approve", self.handle_approve_capability
        )
        app.router.add_post(
            "/admin/capabilities/staging/{name}/reject", self.handle_reject_capability
        )
        app.router.add_get("/admin/capabilities", self.handle_list_capabilities)
        app.router.add_get("/admin/capabilities/{name}", self.handle_get_capability)

        # Trace routes (only if the observability facade is wired)
        if self.observability:
            app.router.add_get("/traces", self.handle_list_traces)
            app.router.add_get("/traces/{trace_id}", self.handle_get_trace)

        # Metrics route (503 when the metrics service is unavailable)
        app.router.add_get("/metrics", self.handle_metrics)

        # GitHub webhook ingress (docs/design/mail.md 「外部来信与信任面」):
        # registered only when the
        # runtime ingress is enabled (adapter configured) and the webhook
        # secret is set — an unconfigured server exposes no endpoint at all.
        if self.github_ingress and self.github_webhook_secret:
            app.router.add_post("/webhooks/github", self.handle_github_webhook)

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

    @web.middleware
    async def _admin_auth_middleware(
        self,
        request: web.Request,
        handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
    ) -> web.StreamResponse:
        """Guard /admin/* with the shared Bearer token (ADR 0013).

        Registered only when ``admin_token`` is configured. CORS preflight
        (OPTIONS) carries no credentials by design and passes through.
        """
        if request.method != "OPTIONS" and request.path.startswith("/admin/"):
            header = request.headers.get("Authorization", "")
            expected = f"Bearer {self.admin_token}"
            if not hmac.compare_digest(header, expected):
                return self._json_response(
                    {"error": "Admin authentication required", "reason": "unauthorized"},
                    status=401,
                )
        return await handler(request)

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
                "timestamp": datetime.now(UTC).isoformat(),
                "version": VERSION,
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
                "count": (len(self.members.get_all_agent_status()) if self.members else 0),
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
        if not self.members:
            return self._json_response(
                {"status": "error", "message": "Agent registry not available"}, status=503
            )
        agents = self.members.get_all_agent_status()
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

        if not self.members:
            return self._json_response(
                {"status": "error", "message": "Agent registry not available"}, status=503
            )
        config = self.members.get_config(agent_id)
        if config is None:
            return self._json_response({"error": f"Agent '{agent_id}' not found"}, status=404)

        state = self.members.get_state(agent_id)

        status = {
            "id": agent_id,
            "name": config.name,
            "state": state,
            "config": {
                "prototype": config.prototype,
                "spawned_by": config.spawned_by,
                "spawn_depth": config.spawn_depth,
            },
        }
        return self._json_response(status)

    async def handle_test_ping(self, request: web.Request) -> web.Response:
        """GET /test/ping - Simple ping endpoint for testing."""
        return self._json_response(
            {
                "status": "ok",
                "pong": True,
                "timestamp": datetime.now(UTC).isoformat(),
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
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            )
        except json.JSONDecodeError:
            return self._json_response({"error": "Invalid JSON"}, status=400)


class RESTServer:
    """REST API server for Tlon.

    Wraps the aiohttp server with lifecycle management.

    Example:
        >>> rest = RESTServer(client_gateway, members=runtime.members, port=8080)
        >>> await rest.start()
        >>> # Server is running
        >>> await rest.stop()
    """

    def __init__(
        self,
        client_gateway: ClientGateway,
        *,
        members: MembersFacade | None = None,
        mail: MailFacade | None = None,
        prototypes: PrototypesFacade | None = None,
        observability: ObservabilityFacade | None = None,
        configs: ConfigFacade | None = None,
        events: EventsFacade | None = None,
        arena: ArenaFacade | None = None,
        capabilities: CapabilitiesFacade | None = None,
        host: str = "127.0.0.1",
        port: int = 8080,
        debug: bool = False,
        admin_token: str | None = None,
        github_webhook_secret: str | None = None,
    ):
        """Initialize the REST server.

        Args:
            client_gateway: The ClientGateway instance
            members: Members facade (agent status/config, member CRUD)
            mail: Mail facade (GET /inbox, GET /members/{member_id}/mail, POST /messages)
            prototypes: Prototypes facade (/admin/prototypes/*)
            observability: Observability facade (/traces, /metrics, logs)
            configs: Config facade (/admin/config/agents listing, agent.yaml)
            events: Events facade (SSE presence attach/detach, GitHub ingress)
            arena: Arena facade (/admin/arena-runs, staging 演武触发/报告)
            capabilities: Capabilities facade (/admin/capabilities/*)
            host: Host to bind to
            port: Port to listen on
            debug: Whether to enable debug/test endpoints (never enable in production)
            admin_token: Shared Bearer token guarding /admin/* (ADR 0013)
            github_webhook_secret: HMAC secret of the GitHub webhook; the
                /webhooks/github route registers only when the events facade
                also reports the ingress enabled
        """
        self.client_gateway = client_gateway
        self.host = host
        self.port = port
        self._handler = RESTAPIHandler(
            client_gateway,
            members=members,
            mail=mail,
            prototypes=prototypes,
            observability=observability,
            configs=configs,
            events=events,
            arena=arena,
            capabilities=capabilities,
            debug=debug,
            admin_token=admin_token,
            github_webhook_secret=github_webhook_secret,
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

        # End active SSE streams first: their handlers run until the
        # client disconnects, and runner.cleanup() would otherwise block
        # for the full shutdown_timeout waiting on these in-flight
        # requests before force-closing them.
        self._handler.close_sse_streams()

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
