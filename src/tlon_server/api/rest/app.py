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
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

from aiohttp import web

from tlon.api.arena import ArenaFacade
from tlon.api.config import ConfigFacade
from tlon.api.events import EventsFacade
from tlon.api.mail import MailFacade
from tlon.api.members import MembersFacade
from tlon.api.observability import ObservabilityFacade
from tlon.api.portable_setup import PortableSetupFacade
from tlon.api.prototypes import PrototypesFacade
from tlon.base.logger import get_logger
from tlon.base.version import VERSION
from tlon_server.core.client_gateway import ClientGateway
from tlon_server.core.local_identity import LocalIdentity, validate_username

from .admin_agents import AdminAgentHandlers
from .admin_arena import AdminArenaHandlers
from .admin_llm import AdminLlmHandlers
from .admin_prototypes import AdminPrototypeHandlers
from .browser_boundary import LOOPBACK_HOSTS, protect_response, trusted_request
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
        identity: LocalIdentity,
        members: MembersFacade | None = None,
        mail: MailFacade | None = None,
        prototypes: PrototypesFacade | None = None,
        observability: ObservabilityFacade | None = None,
        configs: ConfigFacade | None = None,
        events: EventsFacade | None = None,
        arena: ArenaFacade | None = None,
        portable_setup: PortableSetupFacade | None = None,
        debug: bool = False,
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
            debug: Whether to enable debug/test endpoints (never enable in production)
            identity: Persistent instance WebUI identity
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
        self.portable_setup = portable_setup
        self.debug = debug
        self.identity = identity
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
        app = web.Application(middlewares=[self._browser_boundary_middleware])
        app.on_response_prepare.append(protect_response)

        # Add API routes first
        app.router.add_get("/health", self.handle_health)
        app.router.add_get("/identity", self.handle_identity)
        app.router.add_post("/identity", self.handle_initialize_identity)
        app.router.add_get("/config", self.handle_config)
        app.router.add_get("/inbox", self.handle_inbox)
        app.router.add_post("/inbox/read", self.handle_inbox_read)
        app.router.add_get("/members/{member_id}/mail", self.handle_member_mail)
        app.router.add_post("/messages", self.handle_send_message)
        app.router.add_get("/agents", self.handle_list_agents)
        app.router.add_get("/agents/{agent_id}", self.handle_get_agent)
        # Sleeping members wake only at their deadline; dead members cannot restart.
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
        app.router.add_post("/admin/agents/{agent_id}/wake", self.handle_wake_agent)
        app.router.add_post("/admin/agents/{agent_id}/wait", self.handle_wait_agent)
        app.router.add_post("/admin/agents/{agent_id}/sleep", self.handle_sleep_agent)
        app.router.add_post("/admin/agents/{agent_id}/terminate", self.handle_terminate_agent)
        app.router.add_post("/admin/config/agents", self.handle_create_agent)
        app.router.add_get("/admin/config/agents/{agent_id}", self.handle_get_agent_config)
        app.router.add_patch("/admin/config/agents/{agent_id}", self.handle_update_agent)
        app.router.add_delete("/admin/config/agents/{agent_id}", self.handle_delete_agent)
        # Admin LLM registry (ADR 0016 §4): model metadata for the WebUI dropdown
        app.router.add_get("/admin/llm/models", self.handle_list_llm_models)
        app.router.add_get("/admin/llm/setup", self.handle_model_setup_status)
        app.router.add_post("/admin/llm/setup", self.handle_model_setup)
        app.router.add_get("/admin/prototypes/staging", self.handle_list_staging)
        app.router.add_post("/admin/prototypes/staging", self.handle_create_staging_prototype)
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

        return app

    @web.middleware
    async def _browser_boundary_middleware(
        self,
        request: web.Request,
        handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
    ) -> web.StreamResponse:
        """Reject foreign pages before running business handlers or identity writes."""
        if not trusted_request(request):
            return self._json_response(
                {"error": "Untrusted browser request", "reason": "untrusted_origin"}, status=403
            )
        public = (
            request.path == "/health" or request.path == "/ui" or request.path.startswith("/ui/")
        )
        webhook = request.path == "/webhooks/github" and request.method == "POST"
        if not public and not webhook:
            if request.method == "OPTIONS":
                return self._json_response(
                    {
                        "error": "Cross-origin preflight is not supported",
                        "reason": "untrusted_origin",
                    },
                    status=403,
                )
            has_header = request.headers.get("X-Tlon-WebUI") == "1"
            same_origin_events = (
                request.method == "GET"
                and request.path == "/events"
                and request.headers.get("Sec-Fetch-Site") == "same-origin"
            )
            if not has_header and not same_origin_events:
                return self._json_response(
                    {"error": "WebUI request header required", "reason": "untrusted_origin"},
                    status=403,
                )
            if request.path != "/identity" and self.identity.username is None:
                return self._json_response(
                    {"error": "Set your WebUI identity first", "reason": "identity_required"},
                    status=409,
                )
        return await handler(request)

    async def handle_identity(self, request: web.Request) -> web.Response:
        """Return the stable member identity, or null on first use."""
        return self._json_response(
            {"username": self.identity.username, "address": self.identity.address}
        )

    async def handle_initialize_identity(self, request: web.Request) -> web.Response:
        """Initialize the local operator once; no authentication or registration."""
        if self.identity.username is not None:
            return self._json_response(
                {"error": "Identity already initialized", "reason": "identity_exists"}, status=409
            )
        try:
            body = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return self._json_response(
                {"error": "Invalid JSON", "reason": "invalid_json"}, status=400
            )
        try:
            username = validate_username(body.get("username") if isinstance(body, dict) else None)
        except ValueError as exc:
            return self._json_response(
                {"error": str(exc), "reason": "invalid_username"}, status=400
            )
        member_ids = set(self.members.list_ids()) if self.members is not None else set()
        if self.configs is not None:
            member_ids.update(config.id for config in self.configs.list_agent_configs())
        if username.casefold() in {member_id.casefold() for member_id in member_ids}:
            return self._json_response(
                {
                    "error": "Username conflicts with an existing member",
                    "reason": "identity_conflict",
                },
                status=409,
            )
        try:
            self.identity.initialize(username)
        except FileExistsError:
            return self._json_response(
                {"error": "Identity already initialized", "reason": "identity_exists"}, status=409
            )
        except OSError:
            logger.exception("webui_identity_write_failed")
            return self._json_response(
                {"error": "Unable to save identity", "reason": "identity_write_failed"}, status=503
            )
        return self._json_response(
            {"username": self.identity.username, "address": self.identity.address}, status=201
        )

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
                "count": (len(await self.members.get_all_agent_status()) if self.members else 0),
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
        agents = await self.members.get_all_agent_status()
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

        status = await self.members.get_status(agent_id)
        if status is None:
            return self._json_response({"error": f"Agent '{agent_id}' not found"}, status=404)
        status = {
            **status,
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
        identity: LocalIdentity,
        members: MembersFacade | None = None,
        mail: MailFacade | None = None,
        prototypes: PrototypesFacade | None = None,
        observability: ObservabilityFacade | None = None,
        configs: ConfigFacade | None = None,
        events: EventsFacade | None = None,
        arena: ArenaFacade | None = None,
        portable_setup: PortableSetupFacade | None = None,
        host: str = "127.0.0.1",
        port: int = 8080,
        debug: bool = False,
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
            host: Host to bind to
            port: Port to listen on
            debug: Whether to enable debug/test endpoints (never enable in production)
            identity: Persistent instance WebUI identity
            github_webhook_secret: HMAC secret of the GitHub webhook; the
                /webhooks/github route registers only when the events facade
                also reports the ingress enabled
        """
        self.client_gateway = client_gateway
        if host not in LOOPBACK_HOSTS:
            raise ValueError("The local WebUI server must bind to a loopback host")
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
            portable_setup=portable_setup,
            debug=debug,
            identity=identity,
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
        self.port = self._runner.addresses[0][1]

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
