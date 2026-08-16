"""REST API for Tlon Server.

Provides HTTP endpoints for:
- Configuration queries
- Mail inbox retrieval
- Agent management
- Session information
- Client sessions + SSE event stream (protocol v2)
"""

import asyncio
import json
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from aiohttp import web

from tlon.agents.agent_registry import AgentRegistry
from tlon.agents.prototypes import (
    approve_prototype,
    instantiate,
    list_prototypes,
    list_staging_prototypes,
    reject_prototype,
)
from tlon.app.server.core.client_gateway import ClientGateway
from tlon.app.server.core.session_manager import ClientSession
from tlon.base.config import LLMConfig, yaml_load_file, yaml_save
from tlon.base.events.event import Event
from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths
from tlon.base.settings import get_settings
from tlon.organization.mail.mailbox import Mailbox
from tlon.organization.messaging.human_adapter import (
    DEFAULT_USERNAME,
    HUMAN_ID_PREFIX,
    AdapterManager,
)
from tlon.tools.agents.ports import StopRejectedError
from tlon.tools.prototypes.ports import PrototypeError

from .send_mail import SendMailError, parse_send_mail_fields

logger = get_logger(__name__)

# Level ordering for minimum-level filtering of log entries
_LOG_LEVELS = ("debug", "info", "warning", "error", "critical")

# Protocol v2 (HTTP + SSE): the only version POST /sessions accepts.
SESSION_PROTOCOL_VERSION = "2.0.0"

# Valid session modes.
SESSION_MODES = ("chat", "event", "agent")

# SSE heartbeat: a ": ping" comment line every this many seconds.
SSE_HEARTBEAT_SECONDS = 25.0


def _validate_username(username_raw: Any) -> str | None:
    """Normalize a human username; None when invalid.

    The username becomes a directory name — reject empty and anything
    carrying a path separator (traversal guard).
    """
    if not isinstance(username_raw, str):
        return None
    username = username_raw.strip()
    if not username or "/" in username or "\\" in username:
        return None
    return username


def _event_frame(event: Event) -> dict[str, Any]:
    """Serialize an event into the wire frame sent over the SSE stream."""
    payload = event.payload
    if isinstance(payload, dict):
        # Mail delivery payloads nest a Mail under "mail"
        payload = {
            key: value.to_dict() if hasattr(value, "to_dict") else value
            for key, value in payload.items()
        }
    elif hasattr(payload, "to_dict"):
        payload = payload.to_dict()

    frame: dict[str, Any] = {
        "type": "event",
        "event_type": event.type.value,
        "payload": payload,
        "source": event.source,
        "timestamp": event.timestamp,
    }
    # Include tracing fields so clients can observe server-side trace filtering
    if event.trace_id:
        frame["trace_id"] = event.trace_id
    if event.span_id:
        frame["span_id"] = event.span_id
    if event.parent_span_id:
        frame["parent_span_id"] = event.parent_span_id
    return frame


def _recent_log_files(logs_dir: Path, days: int = 2) -> list[Path]:
    """Return existing server-{date}.jsonl files for today and yesterday."""
    files = []
    for offset in range(days - 1, -1, -1):
        day = date.today() - timedelta(days=offset)
        f = logs_dir / f"server-{day:%Y-%m-%d}.jsonl"
        if f.exists():
            files.append(f)
    return files


def _read_log_entries(logs_dir: Path) -> list[dict[str, Any]]:
    """Read structured log entries (JSONL) from today's and yesterday's files."""
    entries: list[dict[str, Any]] = []
    for file in _recent_log_files(logs_dir):
        with file.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(entry, dict):
                    entries.append(entry)
    return entries


def _filter_log_entries(
    entries: list[dict[str, Any]],
    *,
    agent_id: str | None = None,
    trace_id: str | None = None,
    level: str | None = None,
    before: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Filter log entries by any combination of fields, keeping the last `limit`.

    Args:
        entries: Log entries in chronological order
        agent_id: Keep only entries with this agent_id
        trace_id: Keep only entries with this trace_id
        level: Minimum level (debug/info/warning/error/critical)
        before: ISO timestamp; keep only entries logged before it
        limit: Maximum number of entries to return (from the tail)
    """
    min_level = None
    if level:
        level = level.lower()
        if level in _LOG_LEVELS:
            min_level = _LOG_LEVELS.index(level)

    result = []
    for entry in entries:
        if agent_id is not None and entry.get("agent_id") != agent_id:
            continue
        if trace_id is not None and entry.get("trace_id") != trace_id:
            continue
        if min_level is not None:
            entry_level = str(entry.get("level", "")).lower()
            if entry_level not in _LOG_LEVELS or _LOG_LEVELS.index(entry_level) < min_level:
                continue
        if before is not None and str(entry.get("timestamp", "")) >= before:
            continue
        result.append(entry)
    return result[-limit:]


def _parse_limit(request: web.Request, default: int = 100, maximum: int = 1000) -> int:
    """Parse the `limit` query parameter with a cap."""
    try:
        return min(int(request.query.get("limit", default)), maximum)
    except ValueError:
        return default


class RESTAPIHandler:
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
        app.router.add_get("/admin/prototypes", self.handle_list_prototypes)
        app.router.add_post(
            "/admin/prototypes/staging/{name}/approve", self.handle_approve_prototype
        )
        app.router.add_post(
            "/admin/prototypes/staging/{name}/reject", self.handle_reject_prototype
        )

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

        # CORS
        app.router.add_options("/{path:.*}", self.handle_options)

        return app

    def _json_response(self, data: Any, status: int = 200) -> web.Response:
        """Create a JSON response.

        Args:
            data: Data to serialize
            status: HTTP status code

        Returns:
            JSON response
        """
        return web.Response(
            text=json.dumps(data, ensure_ascii=False, default=str),
            status=status,
            content_type="application/json",
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, PATCH, DELETE, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type",
            },
        )

    async def handle_options(self, request: web.Request) -> web.Response:
        """Handle CORS preflight requests."""
        return web.Response(
            status=200,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, PATCH, DELETE, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type",
            },
        )

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

    async def handle_inbox(self, request: web.Request) -> web.Response:
        """GET /inbox - The human member's own mail (mail model, R2).

        Query Parameters:
            username: Human username (default: "guest"); the inbox of
                "human:<username>" is served
            limit: Maximum number of mails (default: 50, max: 200)

        Returns:
            The member's recent mail (inbox + sent merged, chronological)
            or 400 if the username is invalid
        """
        if not self.paths:
            return self._json_response(
                {"status": "error", "message": "Mail storage not available"}, status=503
            )

        username_raw = request.query.get("username")
        if username_raw is None:
            username = DEFAULT_USERNAME
        else:
            username = username_raw.strip()
            # The username becomes a directory name — reject empty and
            # anything carrying a path separator (traversal guard)
            if not username or "/" in username or "\\" in username:
                return self._json_response(
                    {"error": f"Invalid username: {username_raw!r}"}, status=400
                )

        limit = _parse_limit(request, default=50, maximum=200)
        mailbox = Mailbox(self.paths.member_mail_dir(f"{HUMAN_ID_PREFIX}{username}"))
        mails = await mailbox.recent(limit)

        return self._json_response(
            {
                "mails": [mail.to_dict() for mail in mails],
                "count": len(mails),
            }
        )

    async def handle_send_message(self, request: web.Request) -> web.Response:
        """POST /messages - Send a mail as a human member (protocol v2, PR-1).

        The sender's identity comes from the body's ``username``. If the
        human has no live adapter, a transient one is created — first
        contact also creates the persistent mailbox, same semantics as
        the first session attach.

        Request Body:
            username: Human username (default: "guest")
            content: Mail content (required, non-empty)
            to: Recipients (names, ids or full addresses)
            cc: Carbon-copy recipients (optional)
            subject: Optional subject
            in_reply_to: Id of the mail being replied to (optional)
            thread_id: Thread root id inherited from the upstream mail
                (optional)

        Returns:
            202 Accepted with {"mail_id": ...}, or 4xx with
            {"error", "reason"} (e.g. "recipient_unresolved",
            "empty_content", "invalid_to")
        """
        if not self.adapter_manager:
            return self._json_response(
                {"status": "error", "message": "Messaging not available"}, status=503
            )

        try:
            body = await request.json()
        except json.JSONDecodeError:
            return self._json_response(
                {"error": "Invalid JSON", "reason": "invalid_json"}, status=400
            )
        if not isinstance(body, dict):
            return self._json_response(
                {"error": "Request body must be a JSON object", "reason": "invalid_json"},
                status=400,
            )

        username_raw = body.get("username", DEFAULT_USERNAME)
        if not isinstance(username_raw, str):
            return self._json_response(
                {"error": "Field 'username' must be a string", "reason": "invalid_username"},
                status=400,
            )
        username = username_raw.strip()
        # The username becomes a directory name — reject empty and
        # anything carrying a path separator (traversal guard)
        if not username or "/" in username or "\\" in username:
            return self._json_response(
                {"error": f"Invalid username: {username_raw!r}", "reason": "invalid_username"},
                status=400,
            )

        parsed = parse_send_mail_fields(body)
        if isinstance(parsed, SendMailError):
            return self._json_response(
                {"error": parsed.message, "reason": parsed.reason}, status=400
            )

        logger.info(
            "rest_message_received",
            username=username,
            content=parsed.content,
            to=parsed.to,
            cc=parsed.cc,
        )

        try:
            mail = await self.adapter_manager.publish_as(
                username,
                parsed.content,
                to=parsed.to,
                cc=parsed.cc,
                subject=parsed.subject,
                in_reply_to=parsed.in_reply_to,
                thread_id=parsed.thread_id,
            )
        except ValueError as e:
            return self._json_response(
                {"error": str(e), "reason": "recipient_unresolved"}, status=400
            )

        return self._json_response({"mail_id": mail.id}, status=202)

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

    async def handle_list_sessions(self, request: web.Request) -> web.Response:
        """GET /sessions - List active client sessions.

        Returns:
            List of session info
        """
        sessions = self.client_gateway.get_all_sessions()

        session_info = []
        for session in sessions:
            info = {
                "id": session.id,
                "client_type": session.client_type,
                "connected_at": session.connected_at,
                "mode": session.mode,
                "filters": session.filters,
            }
            session_info.append(info)

        return self._json_response(
            {
                "sessions": session_info,
                "count": len(session_info),
            }
        )

    # ==================== Protocol v2: sessions + SSE ====================

    @staticmethod
    def _session_state(session: ClientSession) -> dict[str, Any]:
        """Serialize a session's current state for v2 responses."""
        return {
            "session_id": session.id,
            "username": session.metadata.get("username", DEFAULT_USERNAME),
            "client_type": session.client_type,
            "mode": session.mode,
            "filters": session.filters,
            "connected_at": session.connected_at,
        }

    async def handle_create_session(self, request: web.Request) -> web.Response:
        """POST /sessions - Establish a client session (protocol v2, PR-2).

        The client declares its identity, mode and filters up front; the
        response carries the session id plus the participants/agents
        snapshots. The event handler is registered only when the client
        opens its SSE stream (GET /events).

        Request Body:
            username: Human username (default: "guest")
            client_type: Client type ("tui", "web", ...)
            mode: Initial mode ("chat" | "event" | "agent", default "chat")
            filters: Optional initial event filters
            protocol_version: Must be "2.0.0"

        Returns:
            201 with {session_id, username, client_type, mode, filters,
            connected_at, participants, agents}, or 4xx with
            {"error", "reason"} ("invalid_json", "invalid_username",
            "invalid_client_type", "invalid_mode", "invalid_filters",
            "unsupported_protocol_version")
        """
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return self._json_response(
                {"error": "Invalid JSON", "reason": "invalid_json"}, status=400
            )
        if not isinstance(body, dict):
            return self._json_response(
                {"error": "Request body must be a JSON object", "reason": "invalid_json"},
                status=400,
            )

        protocol_version = body.get("protocol_version")
        if protocol_version != SESSION_PROTOCOL_VERSION:
            return self._json_response(
                {
                    "error": f"Unsupported protocol version: {protocol_version!r}",
                    "reason": "unsupported_protocol_version",
                    "supported_versions": [SESSION_PROTOCOL_VERSION],
                },
                status=400,
            )

        username = _validate_username(body.get("username", DEFAULT_USERNAME))
        if username is None:
            return self._json_response(
                {"error": f"Invalid username: {body.get('username')!r}", "reason": "invalid_username"},
                status=400,
            )

        client_type = body.get("client_type", "unknown")
        if not isinstance(client_type, str) or not client_type.strip():
            return self._json_response(
                {"error": "Field 'client_type' must be a non-empty string",
                 "reason": "invalid_client_type"},
                status=400,
            )

        mode = body.get("mode", "chat")
        if mode not in SESSION_MODES:
            return self._json_response(
                {"error": f"Invalid mode: {mode!r}", "reason": "invalid_mode"}, status=400
            )

        filters = body.get("filters")
        if filters is not None and not isinstance(filters, dict):
            return self._json_response(
                {"error": "Field 'filters' must be an object", "reason": "invalid_filters"},
                status=400,
            )

        session = await self.client_gateway.open_session(
            client_type=client_type.strip(),
            metadata={
                "username": username,
                "protocol_version": SESSION_PROTOCOL_VERSION,
            },
            filters=filters,
        )
        if mode != session.mode:
            await self.client_gateway.update_session_mode(session.id, mode)
            refreshed = self.client_gateway.get_session(session.id)
            if refreshed is not None:
                session = refreshed

        # Snapshots for the client's initial state
        participants = [
            {"id": p.id, "name": p.name, "type": p.type}
            for p in self.client_gateway.get_all_members()
        ]
        agents = self.agent_registry.get_all_agent_status()

        logger.info(
            "rest_session_created",
            session_id=session.id,
            username=username,
            client_type=client_type,
            mode=session.mode,
        )
        return self._json_response(
            {
                **self._session_state(session),
                "participants": participants,
                "agents": agents,
            },
            status=201,
        )

    async def handle_update_session(self, request: web.Request) -> web.Response:
        """PATCH /sessions/{id} - Update session mode and/or filters (v2, PR-2).

        The response body carries the updated session state; no
        confirmation frame or fresh snapshot is pushed on the stream.

        Request Body:
            mode: New mode ("chat" | "event" | "agent"), optional
            filters: New filters object, optional

        Returns:
            200 with the updated session state, 404 if the session does
            not exist, or 4xx with {"error", "reason"} on bad input
        """
        session_id = request.match_info["session_id"]
        session = self.client_gateway.get_session(session_id)
        if session is None:
            return self._json_response(
                {"error": f"Session '{session_id}' not found"}, status=404
            )

        try:
            body = await request.json()
        except json.JSONDecodeError:
            return self._json_response(
                {"error": "Invalid JSON", "reason": "invalid_json"}, status=400
            )
        if not isinstance(body, dict):
            return self._json_response(
                {"error": "Request body must be a JSON object", "reason": "invalid_json"},
                status=400,
            )

        if "mode" in body:
            mode = body["mode"]
            if mode not in SESSION_MODES:
                return self._json_response(
                    {"error": f"Invalid mode: {mode!r}", "reason": "invalid_mode"},
                    status=400,
                )
            await self.client_gateway.update_session_mode(session_id, mode)

        if "filters" in body:
            filters = body["filters"]
            if not isinstance(filters, dict):
                return self._json_response(
                    {"error": "Field 'filters' must be an object", "reason": "invalid_filters"},
                    status=400,
                )
            await self.client_gateway.update_session_filters(session_id, filters)

        session = self.client_gateway.get_session(session_id)
        assert session is not None  # updated above, cannot vanish here
        logger.info("rest_session_updated", session_id=session_id, mode=session.mode)
        return self._json_response(self._session_state(session))

    async def handle_delete_session(self, request: web.Request) -> web.Response:
        """DELETE /sessions/{id} - Disconnect a session (v2, PR-2).

        Idempotent: deleting a missing or already-disconnected session
        returns the same 200. Triggers the full session cleanup (adapter
        detach + handler unregistration + session close); an active SSE
        stream for the session is wound down — its own cleanup path
        repeats these steps harmlessly.

        Returns:
            200 with {"session_id", "status": "disconnected"}
        """
        session_id = request.match_info["session_id"]

        queue = self._sse_queues.get(session_id)
        if queue is not None:
            queue.put_nowait(None)

        if self.adapter_manager:
            await self.adapter_manager.detach(session_id)
        await self.client_gateway.disconnect_client(session_id)

        logger.info("rest_session_deleted", session_id=session_id)
        return self._json_response({"session_id": session_id, "status": "disconnected"})

    async def handle_events(self, request: web.Request) -> web.StreamResponse:
        """GET /events - SSE event stream for a session (protocol v2, PR-2).

        The session_id arrives as a query parameter or the
        ``X-Session-Id`` header. Opening the stream registers the
        session's event handler (the filtering pipeline — mode semantics
        and session filters — lives unchanged in NotificationService)
        and attaches the session to its HumanAdapter (human presence +
        mail fan-out). Closing the stream (client disconnect, failed
        heartbeat write, or DELETE /sessions/{id}) reliably detaches and
        closes the session: SSE disconnect means offline, and clients
        re-POST /sessions to reconnect.

        Event frames are JSON
        ({"type": "event", "event_type", "payload", ...}), one per SSE
        ``data:`` line; a ": ping" comment line is sent every
        SSE_HEARTBEAT_SECONDS as the heartbeat.

        Returns:
            200 text/event-stream, 404 if the session does not exist,
            or 409 if the session already has an active stream
        """
        session_id = request.query.get("session_id") or request.headers.get("X-Session-Id")
        session = self.client_gateway.get_session(session_id) if session_id else None
        if session is None:
            return self._json_response(
                {"error": f"Session '{session_id}' not found"}, status=404
            )
        if session.id in self._sse_queues:
            return self._json_response(
                {
                    "error": f"Session '{session.id}' already has an active event stream",
                    "reason": "stream_active",
                },
                status=409,
            )

        # Register the queue before the first await so a concurrent
        # duplicate stream hits the 409 above instead of racing us.
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._sse_queues[session.id] = queue

        async def event_handler(event: Event) -> None:
            frame = json.dumps(_event_frame(event), ensure_ascii=False, default=str)
            await queue.put(f"data: {frame}\n\n")

        # Wire the session up before prepare() so a client that sees the
        # 200 headers is guaranteed to have its event handler registered
        # and its human adapter attached (same handler doubles as the
        # mail delivery sink: human mail reaches the session through the
        # adapter, not the NotificationService — single-track delivery).
        self.client_gateway.set_event_handler(session.id, event_handler)
        username = session.metadata.get("username", DEFAULT_USERNAME)
        if self.adapter_manager:
            await self.adapter_manager.attach(username, session.id, event_handler)

        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Access-Control-Allow-Origin": "*",
            },
        )
        try:
            await response.prepare(request)
        except Exception:
            self._sse_queues.pop(session.id, None)
            if self.adapter_manager:
                await self.adapter_manager.detach(session.id)
            await self.client_gateway.disconnect_client(session.id)
            raise

        logger.info("sse_connected", session_id=session.id, username=username)
        try:
            while True:
                try:
                    frame = await asyncio.wait_for(
                        queue.get(), timeout=SSE_HEARTBEAT_SECONDS
                    )
                except TimeoutError:
                    frame = ": ping\n\n"
                if frame is None:
                    break
                # A failed write (heartbeat included) means the client is gone
                await response.write(frame.encode("utf-8"))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug("sse_write_error", session_id=session.id, error=str(e))
        finally:
            self._sse_queues.pop(session.id, None)
            if self.adapter_manager:
                await self.adapter_manager.detach(session.id)
            await self.client_gateway.disconnect_client(session.id)
            logger.info("sse_disconnected", session_id=session.id)

        return response

    async def handle_list_participants(self, request: web.Request) -> web.Response:
        """GET /participants - List connected members.

        Returns:
            List of participants
        """
        participants = self.client_gateway.get_all_members()

        participant_info = []
        for p in participants:
            info = {
                "id": p.id,
                "name": p.name,
                "type": p.type,
                "metadata": p.metadata,
            }
            participant_info.append(info)

        return self._json_response(
            {
                "participants": participant_info,
                "count": len(participant_info),
            }
        )

    async def handle_list_agent_configs(self, request: web.Request) -> web.Response:
        """GET /admin/config/agents - List current agent configurations.

        Returns:
            List of agent configurations (sanitized)
        """
        if not self.config_manager:
            return self._json_response(
                {"status": "error", "message": "Config manager not available"}, status=503
            )

        agents = []
        for agent in self.config_manager.agents.values():
            agents.append(
                {
                    "id": agent.id,
                    "name": agent.name,
                    "identity": agent.identity,
                }
            )

        return self._json_response({"status": "success", "agents": agents, "count": len(agents)})

    async def handle_reload_agent(self, request: web.Request) -> web.Response:
        """POST /admin/config/agents/{agent_id}/reload - Hot-adopt a new agent.

        Entry point for out-of-process creation (ops admin API writes
        agent.yaml): the registry reloads the config, registers
        the agent, and starts it unless it is lazy-activated.

        Returns:
            200 with {"status", "agent_id", "result"} (result is one of
            "already-known", "config-not-found", "registered-lazy",
            "started", "start-failed"), or 503 if the registry is
            unavailable
        """
        if not self.agent_registry:
            return self._json_response(
                {"status": "error", "message": "Agent registry not available"}, status=503
            )

        agent_id = request.match_info["agent_id"]
        result = await self.agent_registry.adopt_agent(agent_id)
        return self._json_response(
            {"status": "success", "agent_id": agent_id, "result": result}
        )

    # ---- Admin: agent CRUD + prototype approval (wizard removal, A2) ----

    def _require_admin_context(self) -> web.Response | None:
        """Common guard: registry + paths must be available."""
        if not self.agent_registry or not self.paths:
            return self._json_response(
                {"status": "error", "message": "Admin API not available"}, status=503
            )
        return None

    async def handle_create_agent(self, request: web.Request) -> web.Response:
        """POST /admin/config/agents - create a member from a prototype.

        Body: ``{"prototype", "name", "agent_id"?, "params"?, "persona"?}``.
        The behavior comes from the prototype (behavior genome); ``persona``
        (identity/profile/behavior) is optional creation-time data (ADR
        0006). The new member is hot-adopted by the registry.

        Returns:
            200 with the created config; 400 on bad input / prototype
            errors; 503 when the admin context is unavailable.
        """
        guard = self._require_admin_context()
        if guard is not None:
            return guard
        paths = self.paths
        assert paths is not None and self.agent_registry is not None
        try:
            body = await request.json()
        except Exception:
            return self._json_response(
                {"status": "error", "message": "invalid JSON body"}, status=400
            )
        prototype: str = str(body.get("prototype") or "")
        name: str = str(body.get("name") or "")
        if not prototype or not name:
            return self._json_response(
                {"status": "error", "message": "prototype and name are required"},
                status=400,
            )
        agent_id = str(body.get("agent_id") or f"m-{prototype}-{uuid.uuid4().hex[:8]}")
        params: dict[str, Any] | None = body.get("params")
        persona: dict[str, Any] = body.get("persona") or {}

        try:
            cfg = instantiate(
                prototype, agent_id, name, params=params, tlon_paths=paths
            )
        except PrototypeError as e:
            return self._json_response({"status": "error", "message": str(e)}, status=400)

        config_dir = paths.agent_config_dir(agent_id)
        for field, filename in (
            ("identity", "IDENTITY.md"),
            ("profile", "SOUL.md"),
            ("behavior", "RULES.md"),
        ):
            content = persona.get(field)
            if content:
                (config_dir / filename).write_text(
                    str(content).strip() + "\n", encoding="utf-8"
                )

        result = await self.agent_registry.adopt_agent(agent_id)
        return self._json_response(
            {
                "status": "success",
                "agent_id": agent_id,
                "name": cfg.name,
                "prototype": prototype,
                "adopt": result,
            }
        )

    async def handle_update_agent(self, request: web.Request) -> web.Response:
        """PATCH /admin/config/agents/{agent_id} - edit persona / LLM knobs.

        Body: ``{"persona"?: {identity?, profile?, behavior?}, "llm"?:
        {model_name?, temperature?}}``. Persona files take effect
        immediately (the prompt cache keys on their mtimes); an LLM change
        rewrites ``agent.yaml`` and hot-reloads the config.

        Returns:
            200 with the updated config; 404 unknown agent; 503 admin
            context unavailable.
        """
        guard = self._require_admin_context()
        if guard is not None:
            return guard
        paths = self.paths
        assert paths is not None and self.agent_registry is not None
        agent_id = request.match_info["agent_id"]
        try:
            body = await request.json()
        except Exception:
            return self._json_response(
                {"status": "error", "message": "invalid JSON body"}, status=400
            )

        config_file = paths.agent_config_file(agent_id)
        cfg = yaml_load_file(config_file)
        if cfg is None:
            return self._json_response(
                {"status": "error", "message": f"Agent '{agent_id}' not found"},
                status=404,
            )

        config_dir = paths.agent_config_dir(agent_id)
        persona: dict[str, Any] = body.get("persona") or {}
        for field, filename in (
            ("identity", "IDENTITY.md"),
            ("profile", "SOUL.md"),
            ("behavior", "RULES.md"),
        ):
            content = persona.get(field)
            if content:
                (config_dir / filename).write_text(
                    str(content).strip() + "\n", encoding="utf-8"
                )

        llm: dict[str, Any] | None = body.get("llm")
        if llm:
            cfg.llm = LLMConfig(
                model_name=str(llm.get("model_name") or cfg.llm.model_name),
                temperature=float(llm.get("temperature") or cfg.llm.temperature),
            )
            yaml_save(cfg, paths.tlon_home())
            await self.agent_registry.adopt_agent(agent_id)  # reload config

        return self._json_response(
            {
                "status": "success",
                "agent_id": agent_id,
                "name": cfg.name,
                "prototype": cfg.prototype,
            }
        )

    async def handle_delete_agent(self, request: web.Request) -> web.Response:
        """DELETE /admin/config/agents/{agent_id} - administratively remove
        a member (wizard removal, ADR 0006).

        The member is stopped (sleeps) if running, unregistered, and its
        home directory (archive) is deleted. This is the explicit
        management action — the runtime never removes members on its own.

        Returns:
            200 removed; 404 unknown agent; 409 live sessions; 503 admin
            context unavailable.
        """
        guard = self._require_admin_context()
        if guard is not None:
            return guard
        agent_id = request.match_info["agent_id"]
        try:
            await self.agent_registry.remove_member(agent_id, removed_by="admin-api")
        except StopRejectedError as e:
            return self._json_response(
                {"status": "error", "reason": e.reason, "message": str(e)},
                status=409,
            )
        return self._json_response(
            {"status": "success", "agent_id": agent_id, "removed": True}
        )

    async def handle_list_staging(self, request: web.Request) -> web.Response:
        """GET /admin/prototypes/staging - list LLM-authored prototypes
        awaiting human approval."""
        if not self.paths:
            return self._json_response(
                {"status": "error", "message": "Admin API not available"}, status=503
            )
        protos = list_staging_prototypes(self.paths)
        return self._json_response(
            {
                "status": "success",
                "prototypes": [
                    {
                        "name": p.name,
                        "display_name": p.display_name,
                        "description": p.description,
                        "trusted": p.trusted,
                        "author": p.author,
                    }
                    for p in protos
                ],
            }
        )

    async def handle_list_prototypes(self, request: web.Request) -> web.Response:
        """GET /admin/prototypes - list trusted prototypes (behavior genome)
        a member can be created from (wizard removal: ops picks one to
        instantiate)."""
        if not self.paths:
            return self._json_response(
                {"status": "error", "message": "Admin API not available"}, status=503
            )
        protos = list_prototypes(self.paths)
        return self._json_response(
            {
                "status": "success",
                "prototypes": [
                    {
                        "name": p.name,
                        "display_name": p.display_name,
                        "description": p.description,
                        "trusted": p.trusted,
                        "author": p.author,
                    }
                    for p in protos
                ],
            }
        )

    async def handle_approve_prototype(self, request: web.Request) -> web.Response:
        """POST /admin/prototypes/staging/{name}/approve - promote a staged
        prototype to the trusted library (human approval)."""
        if not self.paths:
            return self._json_response(
                {"status": "error", "message": "Admin API not available"}, status=503
            )
        name = request.match_info["name"]
        try:
            approve_prototype(name, self.paths)
        except PrototypeError as e:
            return self._json_response({"status": "error", "message": str(e)}, status=400)
        return self._json_response({"status": "success", "name": name, "approved": True})

    async def handle_reject_prototype(self, request: web.Request) -> web.Response:
        """POST /admin/prototypes/staging/{name}/reject - delete a staged
        prototype (human rejection)."""
        if not self.paths:
            return self._json_response(
                {"status": "error", "message": "Admin API not available"}, status=503
            )
        name = request.match_info["name"]
        try:
            reject_prototype(name, self.paths)
        except PrototypeError as e:
            return self._json_response({"status": "error", "message": str(e)}, status=400)
        return self._json_response({"status": "success", "name": name, "rejected": True})

    # ---- Admin env settings (wizard removal F): {TLON_HOME}/.env ----

    #: ops settings form fields -> .env keys (merge-write semantics: keys
    #: not edited are left untouched in the file).
    _ENV_FIELDS: dict[str, str] = {
        "base_url": "OPENAI_BASE_URL",
        "api_key": "OPENAI_API_KEY",
        "model_name": "OPENAI_MODEL_NAME",
        "temperature": "OPENAI_TEMPERATURE",
    }

    async def handle_get_settings_env(self, request: web.Request) -> web.Response:
        """GET /admin/settings/env - current LLM env configuration.

        Returns the effective values (real environment variables win over
        .env) with the .env file path. The API key is returned in plain
        text — this is a local admin tool (the operator console is the
        owner of this config; user decision).

        Returns:
            200 with ``{path, configured, values}``; 503 when the admin
            context is unavailable.
        """
        if not self.paths:
            return self._json_response(
                {"status": "error", "message": "Admin API not available"}, status=503
            )
        settings = get_settings()
        return self._json_response(
            {
                "status": "success",
                "path": str(self.paths.env_file()),
                "configured": bool(settings.llm_api_key),
                "values": {
                    "base_url": settings.llm_base_url,
                    "api_key": settings.llm_api_key,
                    "model_name": settings.llm_model_name,
                    "temperature": settings.llm_temperature,
                },
            }
        )

    async def handle_update_settings_env(self, request: web.Request) -> web.Response:
        """PUT /admin/settings/env - write LLM env config to {TLON_HOME}/.env.

        Merge semantics: only the fields present in the body are updated;
        other lines (comments and untouched keys) in the .env are kept.
        The process reads env at startup — the change takes effect after a
        dream restart (Settings is a process-level singleton).

        Returns:
            200 with ``{path, note, updated}``; 400 invalid body; 503 admin
            context unavailable.
        """
        if not self.paths:
            return self._json_response(
                {"status": "error", "message": "Admin API not available"}, status=503
            )
        try:
            body = await request.json()
        except Exception:
            return self._json_response(
                {"status": "error", "message": "invalid JSON body"}, status=400
            )

        # map ops field names -> .env keys; stringify non-string values
        updates: dict[str, str] = {}
        for field, env_key in self._ENV_FIELDS.items():
            if field in body and body[field] is not None:
                updates[env_key] = str(body[field])

        env_file = self.paths.env_file()
        lines: list[str] = []
        if env_file.exists():
            lines = env_file.read_text(encoding="utf-8").splitlines()

        new_lines: list[str] = []
        updated: list[str] = []
        for line in lines:
            stripped = line.strip()
            if "=" in stripped and not stripped.startswith("#"):
                key, _, _ = stripped.partition("=")
                if key in updates:
                    new_lines.append(f"{key}={updates.pop(key)}")
                    updated.append(key)
                    continue
            new_lines.append(line)
        for key, value in updates.items():
            new_lines.append(f"{key}={value}")
            updated.append(key)

        env_file.parent.mkdir(parents=True, exist_ok=True)
        env_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        logger.info("settings_env_updated", path=str(env_file), keys=sorted(updated))
        return self._json_response(
            {
                "status": "success",
                "path": str(env_file),
                "note": "重启 dream 生效（Settings 是进程级单例）",
                "updated": sorted(updated),
            }
        )

    async def handle_list_traces(self, request: web.Request) -> web.Response:
        """GET /traces - List traces with optional filtering.

        Query Parameters:
            limit: Maximum number of traces (default: 100, max: 1000)
            source: Filter by source component

        Returns:
            List of trace records
        """
        if not self.trace_store:
            return self._json_response(
                {"status": "error", "message": "Trace store not available"}, status=503
            )

        try:
            limit = int(request.query.get("limit", 100))
            limit = min(limit, 1000)
        except ValueError:
            limit = 100

        source = request.query.get("source")

        traces = await self.trace_store.list_traces(limit=limit, source=source)

        return self._json_response(
            {
                "traces": [trace.to_dict() for trace in traces],
                "count": len(traces),
            }
        )

    async def handle_get_trace(self, request: web.Request) -> web.Response:
        """GET /traces/{trace_id} - Get a specific trace.

        Returns:
            Complete trace record with all spans
        """
        if not self.trace_store:
            return self._json_response(
                {"status": "error", "message": "Trace store not available"}, status=503
            )

        trace_id = request.match_info["trace_id"]
        trace = await self.trace_store.get_trace(trace_id)

        if not trace:
            return self._json_response({"error": f"Trace '{trace_id}' not found"}, status=404)

        return self._json_response(
            {
                "trace": trace.to_dict(),
                "span_tree": trace.get_span_tree(),
            }
        )

    async def handle_metrics(self, request: web.Request) -> web.Response:
        """GET /metrics - Current in-process metrics snapshot.

        Returns:
            200 with the MetricsService snapshot, or 503 if the metrics
            service is unavailable
        """
        if not self.metrics_service:
            return self._json_response(
                {"status": "error", "message": "Metrics service not available"}, status=503
            )

        return self._json_response(self.metrics_service.snapshot())

    async def handle_get_agent_logs(self, request: web.Request) -> web.Response:
        """GET /agents/{agent_id}/logs - Get agent logs.

        Query Parameters:
            limit: Maximum number of entries (default: 100, max: 1000)
            before: ISO timestamp; only entries logged before it

        Returns:
            Agent log entries (chronological)
        """
        agent_id = request.match_info["agent_id"]

        # Check if agent exists
        config = self.agent_registry.get_config(agent_id)
        if config is None:
            return self._json_response({"error": f"Agent '{agent_id}' not found"}, status=404)

        if not self.logs_dir:
            return self._json_response(
                {"status": "error", "message": "Log storage not available"}, status=503
            )

        logs = _filter_log_entries(
            _read_log_entries(self.logs_dir),
            agent_id=agent_id,
            before=request.query.get("before"),
            limit=_parse_limit(request),
        )

        return self._json_response(
            {
                "agent_id": agent_id,
                "logs": logs,
                "count": len(logs),
            }
        )

    async def handle_list_logs(self, request: web.Request) -> web.Response:
        """GET /logs - Query all server logs with combined filters.

        Query Parameters:
            trace_id: Only entries within this trace (trace -> logs stitching)
            level: Minimum level (debug, info, warning, error, critical)
            agent_id: Only entries from this agent
            limit: Maximum number of entries (default: 100, max: 1000)
            before: ISO timestamp; only entries logged before it

        Returns:
            Log entries (chronological)
        """
        if not self.logs_dir:
            return self._json_response(
                {"status": "error", "message": "Log storage not available"}, status=503
            )

        logs = _filter_log_entries(
            _read_log_entries(self.logs_dir),
            agent_id=request.query.get("agent_id"),
            trace_id=request.query.get("trace_id"),
            level=request.query.get("level"),
            before=request.query.get("before"),
            limit=_parse_limit(request),
        )

        return self._json_response(
            {
                "logs": logs,
                "count": len(logs),
            }
        )

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
