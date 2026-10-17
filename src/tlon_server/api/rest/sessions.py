"""Session endpoints (protocol v2): sessions CRUD, the SSE /events stream,
and the /participants listing."""

import asyncio
import json
from typing import Any

from aiohttp import web

from tlon.base.events.event import Event
from tlon.base.logger import get_logger
from tlon_server.core.session_manager import ClientSession

from .common import RestHandlerBase

logger = get_logger("tlon_server.api.rest")

# Protocol v2 (HTTP + SSE): the only version POST /sessions accepts.
SESSION_PROTOCOL_VERSION = "2.0.0"

# Valid session modes.
SESSION_MODES = ("chat", "event", "agent")

# SSE heartbeat: a ": ping" comment line every this many seconds.
SSE_HEARTBEAT_SECONDS = 25.0


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


class SessionHandlers(RestHandlerBase):
    """Session + SSE route handlers, mixed into ``RESTAPIHandler``."""

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
            "username": session.metadata["username"],
            "client_type": session.client_type,
            "mode": session.mode,
            "filters": session.filters,
            "connected_at": session.connected_at,
        }

    async def handle_create_session(self, request: web.Request) -> web.Response:
        """POST /sessions - Establish a client session (protocol v2, PR-2).

        The client declares its mode and filters up front; the
        response carries the session id plus the participants/agents
        snapshots. The event handler is registered only when the client
        opens its SSE stream (GET /events).

        Request Body:
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

        if "username" in body or "username" in request.query:
            return self._json_response(
                {"error": "Identity is managed by the server", "reason": "identity_managed"},
                status=400,
            )
        username = self.identity.require_username()

        client_type = body.get("client_type", "unknown")
        if not isinstance(client_type, str) or not client_type.strip():
            return self._json_response(
                {
                    "error": "Field 'client_type' must be a non-empty string",
                    "reason": "invalid_client_type",
                },
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
        assert self.members is not None  # wired in production
        agents = await self.members.get_all_agent_status()

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
            return self._json_response({"error": f"Session '{session_id}' not found"}, status=404)

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

        if self.events:
            await self.events.detach_human(session_id)
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
        heartbeat write, DELETE /sessions/{id}, or server shutdown via
        close_sse_streams) reliably detaches and closes the session:
        SSE disconnect means offline, and clients re-POST /sessions to
        reconnect.

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
            return self._json_response({"error": f"Session '{session_id}' not found"}, status=404)
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
        username = session.metadata["username"]
        if self.events:
            await self.events.attach_human(username, session.id, event_handler)

        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )
        try:
            await response.prepare(request)
        except Exception:
            self._sse_queues.pop(session.id, None)
            if self.events:
                await self.events.detach_human(session.id)
            await self.client_gateway.disconnect_client(session.id)
            raise

        # Resolved through the package namespace at call time so tests can
        # monkeypatch tlon_server.api.rest.SSE_HEARTBEAT_SECONDS; the
        # lazy import also avoids an import cycle with the package __init__.
        import tlon_server.api.rest as rest_pkg

        heartbeat = rest_pkg.SSE_HEARTBEAT_SECONDS
        logger.info("sse_connected", session_id=session.id, username=username)
        try:
            while True:
                try:
                    frame = await asyncio.wait_for(queue.get(), timeout=heartbeat)
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
            if self.events:
                await self.events.detach_human(session.id)
            await self.client_gateway.disconnect_client(session.id)
            logger.info("sse_disconnected", session_id=session.id)

        return response

    def close_sse_streams(self) -> None:
        """End all active SSE streams (server shutdown).

        Pushes the None sentinel into every stream's queue so each
        handle_events loop exits promptly through its normal finally
        path (queue pop, adapter detach, session close). Without this,
        aiohttp's runner cleanup would wait the full shutdown_timeout
        (60s default) for these never-ending requests before
        force-closing them.
        """
        for queue in list(self._sse_queues.values()):
            queue.put_nowait(None)

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
