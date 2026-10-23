"""Unit tests for Tlon SDK (protocol v2: REST + SSE).

The server side is simulated with an in-process aiohttp TestServer that
implements the v2 contract: POST /sessions, GET /events (SSE),
PATCH/DELETE /sessions/{id}, POST /messages.

Tests cover:
- Session establishment (request body, snapshot, version negotiation)
- SSE event reception (frame mapping, heartbeat comments ignored)
- Auto-reconnect (re-POST /sessions + stream reopen + on_reconnect)
- Session updates (set_mode / set_filters via PATCH)
- Send receipt (POST /messages 202 / 4xx)
- Disconnect cleanup (DELETE /sessions/{id} + stream abort)
- Adapter filter declaration logic (get_effective_filters) — the actual
  event matching is server-side and tested in test_notification_service.py
"""

import asyncio
import json
from collections.abc import Callable
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from tlon_client.sdk.adapter import TelegramAdapter, TerminalAdapter
from tlon_client.sdk.base_client import BaseClient, ConnectionState, MailSendError
from tlon_client.sdk.client import TlonClient
from tlon_client.sdk.exceptions import (
    AuthenticationError,
    ConnectionError,
    ProtocolError,
)


async def wait_for(condition: Callable[[], bool], timeout: float = 2.0) -> None:
    """Poll until condition() is true or fail after timeout."""
    for _ in range(int(timeout / 0.01)):
        if condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met within timeout")


def make_test_app() -> web.Application:
    """In-process v2 server: sessions + SSE + messages endpoints."""
    app = web.Application()
    app["sessions"] = {}
    app["sse_queues"] = {}
    app["requests"] = []  # captured (method, path, body)
    app["session_counter"] = 0

    async def create_session(request: web.Request) -> web.Response:
        body = await request.json()
        app["requests"].append(("POST", "/sessions", body))
        if body.get("protocol_version") != "2.0.0":
            return web.json_response(
                {
                    "error": "Unsupported protocol version",
                    "reason": "unsupported_protocol_version",
                    "supported_versions": ["2.0.0"],
                },
                status=400,
            )
        if body.get("mode", "chat") not in ("chat", "event", "agent"):
            return web.json_response(
                {"error": "Invalid mode", "reason": "invalid_mode"}, status=400
            )
        app["session_counter"] += 1
        session_id = f"{body.get('client_type', 'unknown')}-{app['session_counter']}"
        session = {
            "session_id": session_id,
            "username": body.get("username", "guest"),
            "client_type": body.get("client_type", "unknown"),
            "mode": body.get("mode", "chat"),
            "filters": body.get("filters") or {},
            "connected_at": "2026-08-12T00:00:00Z",
        }
        app["sessions"][session_id] = session
        return web.json_response(
            {
                **session,
                "participants": [{"id": "human:guest", "name": "guest", "type": "human"}],
                "agents": [{"id": "dev-001", "state": "idle"}],
            },
            status=201,
        )

    async def update_session(request: web.Request) -> web.Response:
        session_id = request.match_info["session_id"]
        session = app["sessions"].get(session_id)
        if session is None:
            return web.json_response({"error": "not found"}, status=404)
        body = await request.json()
        app["requests"].append(("PATCH", f"/sessions/{session_id}", body))
        if "mode" in body:
            session["mode"] = body["mode"]
        if "filters" in body:
            session["filters"] = body["filters"]
        return web.json_response(session)

    async def delete_session(request: web.Request) -> web.Response:
        session_id = request.match_info["session_id"]
        app["requests"].append(("DELETE", f"/sessions/{session_id}", None))
        queue = app["sse_queues"].get(session_id)
        if queue is not None:
            queue.put_nowait(None)
        app["sessions"].pop(session_id, None)
        return web.json_response({"session_id": session_id, "status": "disconnected"})

    async def events(request: web.Request) -> web.StreamResponse:
        session_id = request.query.get("session_id")
        if session_id not in app["sessions"]:
            return web.json_response({"error": "not found"}, status=404)
        if session_id in app["sse_queues"]:
            return web.json_response(
                {"error": "stream active", "reason": "stream_active"}, status=409
            )
        queue: asyncio.Queue = asyncio.Queue()
        app["sse_queues"][session_id] = queue
        resp = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        try:
            while True:
                frame = await queue.get()
                if frame is None:
                    break
                await resp.write(frame.encode("utf-8"))
        finally:
            app["sse_queues"].pop(session_id, None)
        return resp

    async def send_message(request: web.Request) -> web.Response:
        body = await request.json()
        app["requests"].append(("POST", "/messages", body))
        if "FAIL" in body.get("content", ""):
            return web.json_response(
                {"error": "Recipient not found", "reason": "recipient_unresolved"},
                status=400,
            )
        return web.json_response(
            {
                "mail_id": "mail-1",
                "request_id": body["request_id"],
                "local_recipients": ["agent-1"],
                "gateways": [],
                "message": "已入箱",
            },
            status=202,
        )

    async def inbox(request: web.Request) -> web.Response:
        app["requests"].append(("GET", "/inbox", dict(request.query)))
        return web.json_response(
            {
                "mails": [
                    {
                        "id": "mail-1",
                        "from_address": "agent:dev-001",
                        "to": ["human:alice"],
                        "cc": [],
                        "subject": "hi",
                        "content": "hello",
                        "in_reply_to": None,
                        "thread_id": None,
                        "forward_of": None,
                        "timestamp": 123.0,
                        "read": False,
                    }
                ],
                "count": 1,
            }
        )

    app.router.add_post("/sessions", create_session)
    app.router.add_patch("/sessions/{session_id}", update_session)
    app.router.add_delete("/sessions/{session_id}", delete_session)
    app.router.add_get("/events", events)
    app.router.add_post("/messages", send_message)
    app.router.add_get("/inbox", inbox)
    return app


@pytest.fixture
async def server():
    """Start the in-process test server."""
    test_server = TestServer(make_test_app())
    await test_server.start_server()
    yield test_server
    await test_server.close()


@pytest.fixture
def base_url(server):
    return str(server.make_url(""))


class TestBaseClient:
    """Tests for BaseClient against the in-process v2 server."""

    @pytest.fixture
    async def client(self, base_url):
        client = BaseClient(base_url=base_url, auto_reconnect=False)
        yield client
        await client.disconnect()

    def test_init(self, base_url):
        """Test BaseClient initialization."""
        client = BaseClient(base_url=base_url)
        assert client.base_url == base_url.rstrip("/")
        assert client.state == ConnectionState.DISCONNECTED
        assert not client.is_connected
        assert not client.is_authenticated
        assert client.session_id is None

    async def test_connect(self, client):
        """connect() creates the HTTP session without establishing a session."""
        assert await client.connect() is True
        assert client.state == ConnectionState.CONNECTED
        assert client.is_connected
        assert client.session_id is None

    async def test_authenticate_establishes_session(self, client, server):
        """authenticate() POSTs /sessions and stores the returned snapshot."""
        await client.connect()
        result = await client.authenticate(
            "tui", username="alice", filters={"event_types": ["mail.*"]}, mode="event"
        )

        assert result is True
        assert client.is_authenticated
        assert client.session_id == "tui-1"
        assert client.protocol_version == "2.0.0"
        assert client.username == "alice"
        assert client.mode == "event"
        assert client.participants == [{"id": "human:guest", "name": "guest", "type": "human"}]
        assert client.agents == [{"id": "dev-001", "state": "idle"}]

        # The request body follows the v2 contract
        method, path, body = server.app["requests"][0]
        assert (method, path) == ("POST", "/sessions")
        assert body == {
            "client_type": "tui",
            "mode": "event",
            "protocol_version": "2.0.0",
            "username": "alice",
            "filters": {"event_types": ["mail.*"]},
        }

        # The SSE stream was opened for the new session
        await wait_for(lambda: "tui-1" in server.app["sse_queues"])

    async def test_authenticate_not_connected(self, client):
        """Authentication without connect() raises ConnectionError."""
        with pytest.raises(ConnectionError):
            await client.authenticate("tui")

    async def test_authenticate_version_mismatch(self, client, monkeypatch):
        """A rejected protocol version raises ProtocolError."""
        monkeypatch.setattr(BaseClient, "PROTOCOL_VERSION", "9.9.9")
        await client.connect()
        with pytest.raises(ProtocolError) as exc_info:
            await client.authenticate("tui")

        assert "2.0.0" in exc_info.value.server_versions
        assert not client.is_authenticated

    async def test_authenticate_invalid_field(self, client):
        """A 4xx without a version reason raises AuthenticationError."""
        await client.connect()
        with pytest.raises(AuthenticationError):
            await client.authenticate("tui", mode="bogus")

    async def test_event_received(self, client, server):
        """SSE event frames are mapped and dispatched to event handlers."""
        events: list[dict] = []
        client.on_event(events.append)

        await client.connect()
        await client.authenticate("tui")
        await wait_for(lambda: "tui-1" in server.app["sse_queues"])

        frame = {
            "type": "event",
            "event_type": "mail.delivered",
            "payload": {"content": "hello"},
            "source": "dev-001",
            "timestamp": 123.0,
            "trace_id": "trace-1",
        }
        server.app["sse_queues"]["tui-1"].put_nowait(f"data: {json.dumps(frame)}\n\n")

        await wait_for(lambda: len(events) == 1)
        assert events[0]["type"] == "mail.delivered"
        assert events[0]["payload"] == {"content": "hello"}
        assert events[0]["source"] == "dev-001"
        assert events[0]["trace_id"] == "trace-1"

    async def test_heartbeat_comment_ignored(self, client, server):
        """': ping' SSE comment lines are ignored."""
        events: list[dict] = []
        client.on_event(events.append)

        await client.connect()
        await client.authenticate("tui")
        await wait_for(lambda: "tui-1" in server.app["sse_queues"])

        queue = server.app["sse_queues"]["tui-1"]
        queue.put_nowait(": ping\n\n")
        frame = {"type": "event", "event_type": "agent.idle", "payload": {}}
        queue.put_nowait(f"data: {json.dumps(frame)}\n\n")

        await wait_for(lambda: len(events) == 1)
        assert events[0]["type"] == "agent.idle"

    async def test_update_session(self, client, server):
        """update_session() PATCHes mode/filters and syncs local state."""
        await client.connect()
        await client.authenticate("tui")

        result = await client.update_session(mode="event", filters={"trace_id": "t-1"})

        assert result is True
        assert client.mode == "event"
        assert client.filters == {"trace_id": "t-1"}
        patch_req = [r for r in server.app["requests"] if r[0] == "PATCH"][0]
        assert patch_req[1] == "/sessions/tui-1"
        assert patch_req[2] == {"mode": "event", "filters": {"trace_id": "t-1"}}

    async def test_update_session_not_authenticated(self, client):
        """update_session() without a session is rejected locally."""
        await client.connect()
        assert await client.update_session(mode="event") is False

    async def test_disconnect_deletes_session(self, client, server):
        """disconnect() DELETEs the session and aborts the SSE stream."""
        await client.connect()
        await client.authenticate("tui")
        await wait_for(lambda: "tui-1" in server.app["sse_queues"])

        await client.disconnect()

        assert client.state == ConnectionState.DISCONNECTED
        assert client.session_id is None
        delete_req = [r for r in server.app["requests"] if r[0] == "DELETE"]
        assert delete_req and delete_req[0][1] == "/sessions/tui-1"
        # The server-side stream was wound down
        await wait_for(lambda: "tui-1" not in server.app["sse_queues"])

    async def test_state_change_handler(self, client):
        """State change handlers observe the connect transitions."""
        states: list[ConnectionState] = []
        client.on_state_change(states.append)

        await client.connect()

        assert ConnectionState.CONNECTED in states


class TestReconnect:
    """Auto-reconnect: re-POST /sessions + reopen the SSE stream."""

    async def test_reconnect_reestablishes_session(self, base_url, server):
        client = BaseClient(base_url=base_url, auto_reconnect=True, reconnect_delay=0.01)
        on_reconnect = MagicMock()
        client.on_reconnect(on_reconnect)
        try:
            await client.connect()
            await client.authenticate("tui", username="alice", mode="event")
            await wait_for(lambda: "tui-1" in server.app["sse_queues"])
            on_reconnect.assert_not_called()

            # Simulate the server dropping the stream
            server.app["sse_queues"]["tui-1"].put_nowait(None)

            # The client re-POSTs /sessions (same params) and reopens SSE
            await wait_for(lambda: client.session_id == "tui-2")
            await wait_for(lambda: "tui-2" in server.app["sse_queues"])
            assert client.is_authenticated
            on_reconnect.assert_called_once_with()

            posts = [r for r in server.app["requests"] if r[1] == "/sessions"]
            assert len(posts) == 2
            assert posts[1][2]["username"] == "alice"
            assert posts[1][2]["mode"] == "event"
        finally:
            await client.disconnect()

    async def test_events_flow_after_reconnect(self, base_url, server):
        """The reopened stream delivers events to the same handlers."""
        client = BaseClient(base_url=base_url, auto_reconnect=True, reconnect_delay=0.01)
        events: list[dict] = []
        client.on_event(events.append)
        try:
            await client.connect()
            await client.authenticate("tui")
            await wait_for(lambda: "tui-1" in server.app["sse_queues"])
            server.app["sse_queues"]["tui-1"].put_nowait(None)
            await wait_for(lambda: "tui-2" in server.app["sse_queues"])

            frame = {"type": "event", "event_type": "mail.delivered", "payload": {}}
            server.app["sse_queues"]["tui-2"].put_nowait(f"data: {json.dumps(frame)}\n\n")
            await wait_for(lambda: len(events) == 1)
        finally:
            await client.disconnect()

    async def test_no_reconnect_when_disabled(self, base_url, server):
        """Without auto_reconnect, a dropped stream stays disconnected."""
        client = BaseClient(base_url=base_url, auto_reconnect=False)
        try:
            await client.connect()
            await client.authenticate("tui")
            await wait_for(lambda: "tui-1" in server.app["sse_queues"])

            server.app["sse_queues"]["tui-1"].put_nowait(None)

            await wait_for(lambda: client.state == ConnectionState.DISCONNECTED)
            await asyncio.sleep(0.05)
            assert server.app["session_counter"] == 1
        finally:
            await client.disconnect()


class TestHandlerDispatch:
    """Handler dispatch: task tracking, exception isolation, _fire convergence."""

    @pytest.fixture
    async def client(self, base_url):
        client = BaseClient(base_url=base_url, auto_reconnect=False)
        yield client
        await client.disconnect()

    async def test_dispatch_event_forwards_event(self):
        client = BaseClient()
        handler = MagicMock()
        client.on_event(handler)

        event = {"type": "mail.delivered", "payload": {}}
        client._dispatch_event(event)

        handler.assert_called_once_with(event)

    async def test_dispatch_message_forwards_message(self):
        client = BaseClient()
        handler = MagicMock()
        client.on_message(handler)

        msg = {"type": "notice"}
        client._dispatch_message(msg)

        handler.assert_called_once_with(msg)

    async def test_dispatch_error_forwards_error(self):
        client = BaseClient()
        handler = MagicMock()
        client.on_error(handler)

        client._dispatch_error("boom")

        handler.assert_called_once_with("boom")

    async def test_notify_state_change_forwards_state(self):
        client = BaseClient()
        handler = MagicMock()
        client.on_state_change(handler)

        client._notify_state_change(ConnectionState.CONNECTED)

        handler.assert_called_once_with(ConnectionState.CONNECTED)

    async def test_notify_reconnect_calls_with_no_args(self):
        client = BaseClient()
        handler = MagicMock()
        client.on_reconnect(handler)

        client._notify_reconnect()

        handler.assert_called_once_with()

    async def test_coroutine_handler_task_tracked_and_released(self, client, server):
        """A coroutine handler's task is held in _handler_tasks (no GC) and
        discarded once the handler completes."""
        started = asyncio.Event()
        release = asyncio.Event()

        async def handler(event: dict) -> None:
            started.set()
            await release.wait()

        client.on_event(handler)
        await client.connect()
        await client.authenticate("tui")
        await wait_for(lambda: "tui-1" in server.app["sse_queues"])

        frame = {"type": "event", "event_type": "mail.delivered", "payload": {}}
        server.app["sse_queues"]["tui-1"].put_nowait(f"data: {json.dumps(frame)}\n\n")

        await wait_for(lambda: started.is_set())
        assert len(client._handler_tasks) == 1

        release.set()
        await wait_for(lambda: not client._handler_tasks)

    async def test_coroutine_handler_exception_logged_and_isolated(
        self, client, server, monkeypatch
    ):
        """A failing coroutine handler is logged under the path's event name,
        does not affect other handlers, and its task leaves the set."""
        mock_logger = MagicMock()
        monkeypatch.setattr("tlon_client.sdk.base_client.logger", mock_logger)
        received: list[dict] = []

        async def failing(event: dict) -> None:
            raise RuntimeError("handler blew up")

        client.on_event(failing)
        client.on_event(received.append)
        await client.connect()
        await client.authenticate("tui")
        await wait_for(lambda: "tui-1" in server.app["sse_queues"])

        frame = {"type": "event", "event_type": "mail.delivered", "payload": {}}
        server.app["sse_queues"]["tui-1"].put_nowait(f"data: {json.dumps(frame)}\n\n")

        await wait_for(lambda: len(received) == 1)
        await wait_for(lambda: not client._handler_tasks)
        error_events = [c[0][0] for c in mock_logger.error.call_args_list]
        assert "event_handler_error" in error_events

    async def test_sync_handler_exception_logged_and_loop_continues(self, monkeypatch):
        """A failing sync handler is logged and later handlers still run."""
        mock_logger = MagicMock()
        monkeypatch.setattr("tlon_client.sdk.base_client.logger", mock_logger)
        client = BaseClient()
        received: list[dict] = []

        def failing(event: dict) -> None:
            raise RuntimeError("sync blew up")

        client.on_event(failing)
        client.on_event(received.append)

        event = {"type": "mail.delivered"}
        client._dispatch_event(event)

        assert received == [event]
        mock_logger.error.assert_called_once()
        assert mock_logger.error.call_args[0][0] == "event_handler_error"

    async def test_disconnect_cancels_pending_handler_tasks(self, client, server):
        """disconnect() cancels in-flight handler tasks and clears the set."""
        started = asyncio.Event()

        async def handler(event: dict) -> None:
            started.set()
            await asyncio.Event().wait()  # never returns on its own

        client.on_event(handler)
        await client.connect()
        await client.authenticate("tui")
        await wait_for(lambda: "tui-1" in server.app["sse_queues"])

        frame = {"type": "event", "event_type": "mail.delivered", "payload": {}}
        server.app["sse_queues"]["tui-1"].put_nowait(f"data: {json.dumps(frame)}\n\n")

        await wait_for(lambda: started.is_set())
        task = next(iter(client._handler_tasks))

        await client.disconnect()

        assert task.cancelled()
        assert not client._handler_tasks


class TestTerminalAdapter:
    """Tests for TerminalAdapter class."""

    @pytest.fixture
    def adapter(self):
        """Create a TerminalAdapter instance."""
        return TerminalAdapter()

    def test_init(self, adapter):
        """Test TerminalAdapter initialization."""
        assert adapter.client_type == "tui"
        assert "trace" in adapter.capabilities
        assert "filter" in adapter.capabilities
        assert adapter.mode == "chat"

    def test_set_mode(self, adapter):
        """Test setting display mode."""
        adapter.set_mode("event")
        assert adapter.mode == "event"

        adapter.set_mode("agent")
        assert adapter.mode == "agent"

        with pytest.raises(ValueError):
            adapter.set_mode("invalid")

    def test_effective_filters_default(self, adapter):
        """By default a terminal client declares no filters."""
        assert adapter.get_effective_filters() == {}

    def test_effective_filters_with_trace_filter(self, adapter):
        """Trace filter is declared as a trace_id filter."""
        adapter.set_trace_filter("trace-abc-123")
        assert adapter.get_effective_filters() == {"trace_id": "trace-abc-123"}

    def test_effective_filters_with_event_type_filter(self, adapter):
        """User event filters are passed through."""
        adapter.set_event_filters({"event_types": ["mail.delivered", "agent.response"]})
        assert adapter.get_effective_filters() == {
            "event_types": ["mail.delivered", "agent.response"]
        }

    def test_effective_filters_high_frequency_exclusion(self, adapter):
        """High-frequency exclusion only applies in event mode."""
        adapter.set_exclude_high_frequency(True)

        # In chat mode: no exclusion declared (chat mode never carries these)
        adapter.set_mode("chat")
        assert "exclude_event_types" not in adapter.get_effective_filters()

        # In event mode: exclusion declared for the server to enforce
        adapter.set_mode("event")
        filters = adapter.get_effective_filters()
        assert set(filters["exclude_event_types"]) == adapter.HIGH_FREQUENCY_EVENTS

        # In agent mode: no exclusion declared
        adapter.set_mode("agent")
        assert "exclude_event_types" not in adapter.get_effective_filters()

    def test_clear_filters(self, adapter):
        """Test clearing filters."""
        adapter.set_trace_filter("trace-123")
        adapter.set_event_filters({"event_types": ["mail.delivered"]})

        adapter.clear_filters()

        assert adapter._trace_filter is None
        assert adapter._event_filters is None
        assert adapter.get_effective_filters() == {}

    def test_get_auth_metadata(self, adapter):
        """Test auth metadata generation."""
        metadata = adapter.get_auth_metadata()

        assert "client_version" in metadata
        assert "platform" in metadata


class TestTelegramAdapter:
    """Tests for TelegramAdapter class."""

    @pytest.fixture
    def adapter(self):
        """Create a TelegramAdapter instance."""
        return TelegramAdapter()

    def test_init(self, adapter):
        """A Telegram client is a mail-only chat channel."""
        assert adapter.client_type == "telegram"
        assert adapter.mode == "chat"
        assert "filter" in adapter.capabilities
        assert "trace" not in adapter.capabilities
        assert "history" not in adapter.capabilities

    def test_effective_filters_default_mail_only(self, adapter):
        """By default a Telegram client declares mail.* delivery only."""
        assert adapter.get_effective_filters() == {"event_types": ["mail.*"]}

    def test_effective_filters_user_event_types_take_over(self, adapter):
        """Explicit user event_types replace the mail-only default."""
        adapter.set_event_filters({"event_types": ["mail.delivered"]})
        assert adapter.get_effective_filters() == {"event_types": ["mail.delivered"]}

    def test_effective_filters_with_trace_filter(self, adapter):
        """Trace filter is declared alongside the mail-only default."""
        adapter.set_trace_filter("trace-abc-123")
        assert adapter.get_effective_filters() == {
            "event_types": ["mail.*"],
            "trace_id": "trace-abc-123",
        }

    def test_clear_filters_restores_default(self, adapter):
        """Clearing user filters falls back to the mail-only default."""
        adapter.set_event_filters({"event_types": ["agent.*"]})

        adapter.clear_filters()

        assert adapter.get_effective_filters() == {"event_types": ["mail.*"]}


class TestTlonClient:
    """Tests for TlonClient class."""

    @pytest.fixture
    def client(self, base_url):
        """Create a TlonClient instance."""
        return TlonClient(base_url=base_url, adapter=TerminalAdapter())

    def test_init(self, client, base_url):
        """Test TlonClient initialization."""
        assert client._base.base_url == base_url.rstrip("/")
        assert isinstance(client.adapter, TerminalAdapter)

    def test_properties(self, client):
        """Test TlonClient properties."""
        # Initially not connected
        assert not client.is_connected
        assert not client.is_authenticated
        assert client.session_id is None

    async def test_context_manager(self, client):
        """Test async context manager."""
        client.connect = AsyncMock(return_value=True)
        client.disconnect = AsyncMock()

        async with client as c:
            assert c is client

        client.connect.assert_called_once()
        client.disconnect.assert_called_once()

    async def test_authenticate_declares_adapter_filters(self, client):
        """authenticate() passes client_type/mode/filters to the session request."""
        client._base = AsyncMock()
        client._base.authenticate = AsyncMock(return_value=True)

        result = await client.authenticate(username="alice")

        assert result is True
        call_kwargs = client._base.authenticate.call_args.kwargs
        assert call_kwargs["client_type"] == "tui"
        assert call_kwargs["mode"] == "chat"
        assert call_kwargs["username"] == "alice"
        assert call_kwargs["filters"] == client.adapter.get_effective_filters()

    async def test_send_message(self, client):
        """send_message posts to /messages with the session's username."""
        client._base = AsyncMock()
        client._base.username = "alice"
        client._base.submit_mail = AsyncMock(
            return_value={"mail_id": "mail-1", "message": "已入箱"}
        )

        result = await client.send_message("Hello", to=["agent-1"])

        assert result["mail_id"] == "mail-1"
        client._base.submit_mail.assert_called_once()
        body = client._base.submit_mail.call_args[0][0]
        assert body["request_id"]
        assert body["content"] == "Hello"
        assert body["to"] == ["agent-1"]
        assert body["username"] == "alice"

    async def test_send_message_reply_headers(self, client):
        """reply_to/thread_id map to the server's in_reply_to/thread_id fields."""
        client._base = AsyncMock()
        client._base.username = None
        client._base.submit_mail = AsyncMock(
            return_value={"mail_id": "mail-2", "message": "已入箱"}
        )

        await client.send_message("Got it", to=["agent-1"], reply_to="mail-1", thread_id="thread-1")

        body = client._base.submit_mail.call_args[0][0]
        assert body["in_reply_to"] == "mail-1"
        assert body["thread_id"] == "thread-1"
        assert "reply_to" not in body
        assert "username" not in body

    async def test_send_message_rejected(self, client):
        """A 4xx receipt (rest_post -> None) makes send_message return False."""
        client._base = AsyncMock()
        client._base.username = None
        client._base.submit_mail = AsyncMock(
            side_effect=MailSendError("rejected", request_id="r", reason="invalid_recipients")
        )

        with pytest.raises(MailSendError):
            await client.send_message("Hi", to=["ghost"])

    async def test_set_trace_filter(self, client):
        """Test set_trace_filter method."""
        client._base = AsyncMock()
        client._base.update_session = AsyncMock(return_value=True)

        result = await client.set_trace_filter("trace-123")

        assert result is True
        assert client.adapter._trace_filter == "trace-123"
        client._base.update_session.assert_called_once()
        assert client._base.update_session.call_args.kwargs["filters"]["trace_id"] == "trace-123"

    async def test_set_trace_filter_clear(self, client):
        """Test clearing trace filter."""
        client._base = AsyncMock()
        client._base.update_session = AsyncMock(return_value=True)

        # First set a filter
        await client.set_trace_filter("trace-123")
        # Then clear it
        result = await client.set_trace_filter(None)

        assert result is True
        assert client.adapter._trace_filter is None

    async def test_set_mode_syncs_mode_and_filters(self, client):
        """set_mode sends the mode and re-synced filters in one PATCH."""
        client._base = AsyncMock()
        client._base.update_session = AsyncMock(return_value=True)

        result = await client.set_mode("event")

        assert result is True
        assert client.adapter.mode == "event"
        client._base.update_session.assert_called_once()
        call_kwargs = client._base.update_session.call_args.kwargs
        assert call_kwargs["mode"] == "event"
        assert call_kwargs["filters"] == client.adapter.get_effective_filters()

    async def test_on_event_dispatches_without_local_filtering(self, client):
        """Events are filtered server-side; _on_event dispatches everything."""
        handler = MagicMock()
        client.on_event(handler)

        # Simulate receiving a message event
        message_event = {
            "type": "mail.delivered",
            "payload": {"content": "Hello"},
        }
        client._on_event(message_event)
        handler.assert_called_once_with(message_event)

        handler.reset_mock()

        # Agent events are dispatched too (server already decided to send them)
        agent_event = {
            "type": "agent.idle",
            "payload": {"agent_id": "dev-001"},
        }
        client._on_event(agent_event)
        handler.assert_called_once_with(agent_event)

    async def test_rest_api_methods(self, client):
        """Test REST API wrapper methods."""
        client._base = AsyncMock()
        client._base.rest_get = AsyncMock(
            side_effect=[
                {"agents": [{"id": "agent-1"}]},
                {"trace_id": "t-1"},
            ]
        )

        agents = await client.get_agents()
        assert len(agents) == 1
        assert agents[0]["id"] == "agent-1"

        trace = await client.get_trace("t-1")
        assert trace["trace_id"] == "t-1"

    async def test_get_inbox(self, client):
        """get_inbox returns the mails list and passes username/limit params."""
        client._base = AsyncMock()
        client._base.rest_get = AsyncMock(
            return_value={"mails": [{"id": "mail-1", "read": False}], "count": 1}
        )

        mails = await client.get_inbox(username="alice", limit=20)

        assert mails == [{"id": "mail-1", "read": False}]
        client._base.rest_get.assert_called_once_with("/inbox", {"username": "alice", "limit": 20})

    async def test_get_inbox_omits_username_when_unset(self, client):
        """Without a username the server-side default ("guest") applies."""
        client._base = AsyncMock()
        client._base.rest_get = AsyncMock(return_value={"mails": [], "count": 0})

        mails = await client.get_inbox()

        assert mails == []
        client._base.rest_get.assert_called_once_with("/inbox", {"limit": 50})

    async def test_get_inbox_request_failed(self, client):
        """A failed request (rest_get -> None) yields an empty list."""
        client._base = AsyncMock()
        client._base.rest_get = AsyncMock(return_value=None)

        assert await client.get_inbox(username="alice") == []


class TestTlonClientIntegration:
    """TlonClient against the in-process v2 server (end-to-end transport)."""

    async def test_full_flow(self, base_url, server):
        """connect → authenticate → event → send → set_mode → disconnect."""
        events: list[dict] = []
        async with TlonClient(base_url=base_url, auto_reconnect=False) as client:
            assert await client.authenticate(username="alice") is True
            assert client.session_id == "tui-1"
            client.on_event(events.append)
            await wait_for(lambda: "tui-1" in server.app["sse_queues"])

            # Event reception
            frame = {
                "type": "event",
                "event_type": "mail.delivered",
                "payload": {"content": "hi"},
            }
            server.app["sse_queues"]["tui-1"].put_nowait(f"data: {json.dumps(frame)}\n\n")
            await wait_for(lambda: len(events) == 1)
            assert events[0]["type"] == "mail.delivered"

            # Send receipt (202 + mail_id)
            assert (await client.send_message("Hello", to=["dev-001"]))["mail_id"] == "mail-1"
            msg_req = [r for r in server.app["requests"] if r[1] == "/messages"][0]
            assert msg_req[2]["username"] == "alice"

            # Send rejection (4xx) -> False
            with pytest.raises(MailSendError):
                await client.send_message("FAIL now", to=["ghost"])

            # set_mode via PATCH
            assert await client.set_mode("event") is True
            patch_req = [r for r in server.app["requests"] if r[0] == "PATCH"][0]
            assert patch_req[2]["mode"] == "event"

        # Context-manager exit deleted the session
        assert any(r[0] == "DELETE" for r in server.app["requests"])

    async def test_get_inbox_integration(self, base_url, server):
        """get_inbox hits GET /inbox with the declared query params."""
        async with TlonClient(base_url=base_url, auto_reconnect=False) as client:
            mails = await client.get_inbox(username="alice", limit=5)

        assert len(mails) == 1
        assert mails[0]["id"] == "mail-1"
        assert mails[0]["read"] is False
        inbox_req = [r for r in server.app["requests"] if r[1] == "/inbox"][0]
        assert inbox_req[0] == "GET"
        assert inbox_req[2] == {"username": "alice", "limit": "5"}


async def test_uncertain_mail_send_retains_request_id() -> None:
    """A lost HTTP response cannot be reported as a definite rejection."""
    client = TlonClient("http://localhost:8080")
    client._base._http = MagicMock()
    client._base._http.post.side_effect = TimeoutError("response lost")
    with pytest.raises(MailSendError) as caught:
        await client.send_message("hello", to=["alice"], request_id="stable-request")
    assert caught.value.reason == "submission_unknown"
    assert caught.value.request_id == "stable-request"
    body = client._base._http.post.call_args.kwargs["json"]
    assert body["request_id"] == "stable-request"
