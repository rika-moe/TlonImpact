"""Unit tests for the protocol v2 sessions endpoints + SSE stream (PR-2).

The CRUD endpoints (POST/PATCH/DELETE /sessions) are tested by calling
the handlers directly with stub requests. The SSE endpoint (GET
/events) is tested against a real in-process aiohttp server
(TestServer/TestClient) with a live EventBus.

Covers:
- POST /sessions: 201 + session/participants/agents snapshot, protocol
  version check, field validation
- PATCH /sessions/{id}: mode/filters update, 404, validation
- DELETE /sessions/{id}: idempotent disconnect, cleanup
- GET /events: event frames (WS wire format), heartbeat comment lines,
  404/409, disconnect cleanup (session close + handler unregister +
  adapter detach), chat-mode single-track mail delivery + mode filter
"""

import asyncio
import json
from collections.abc import AsyncGenerator, Callable
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from aiohttp import ClientSession
from aiohttp.test_utils import TestClient, TestServer

from tlon.agents.lifecycle import StartRequest, StopRequest
from tlon.api.events import EventsFacade
from tlon.api.members import MembersFacade
from tlon.base.config import AgentConfig
from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus
from tlon.base.paths import TlonPaths
from tlon.organization.mail.mail import Mail
from tlon.organization.mail.post_office import PostOffice
from tlon.organization.mail.resolver import RecipientResolver
from tlon.organization.messaging.human_adapter import AdapterManager
from tlon_server.api import rest as rest_module
from tlon_server.api.rest import RESTAPIHandler, RESTServer
from tlon_server.core.client_gateway import ClientGateway
from tlon_server.core.member_directory import MemberDirectory
from tlon_server.core.notification_service import NotificationService
from tlon_server.core.session_manager import SessionManager


class StubRequest:
    """Minimal stand-in for aiohttp.web.Request (json body + path params)."""

    def __init__(self, body: Any = None, session_id: str | None = None):
        self._body = body
        self.match_info = {"session_id": session_id} if session_id else {}

    async def json(self) -> Any:
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class MockAgentInfoProvider:
    """Mock AgentInfoProvider for testing."""

    def __init__(self, configs: dict[str, AgentConfig]):
        self._configs = configs

    def get_config(self, agent_id: str) -> AgentConfig | None:
        return self._configs.get(agent_id)

    def get_all_ids(self) -> set[str]:
        return set(self._configs.keys())


AGENT_CONFIGS = {"xie-yunshu": AgentConfig(id="xie-yunshu", name="谢云舒")}
AGENT_STATUS = [{"id": "xie-yunshu", "name": "谢云舒", "state": "running"}]


@pytest.fixture
async def bus() -> AsyncGenerator[EventBus]:
    """Provide a running EventBus and shut it down after the test."""
    event_bus = EventBus()
    await event_bus.initialize()
    yield event_bus
    await event_bus.shutdown()


@pytest.fixture
async def manager(bus: EventBus, tmp_path: Path) -> AsyncGenerator[AdapterManager]:
    """Provide an initialized AdapterManager (isolated tlon home)."""
    paths = TlonPaths(home=tmp_path)
    resolver = RecipientResolver(MockAgentInfoProvider(AGENT_CONFIGS), paths)
    office = PostOffice(bus, resolver, paths)
    mgr = AdapterManager(bus, office, resolver, paths)
    await mgr.initialize()
    yield mgr
    await mgr.shutdown()


@pytest.fixture
def registry() -> MagicMock:
    """Provide a stub AgentRegistry with one agent."""
    reg = MagicMock()
    reg.get_all_ids.return_value = list(AGENT_CONFIGS)
    reg.get_config.side_effect = AGENT_CONFIGS.get
    reg.get_all_agent_status.return_value = list(AGENT_STATUS)
    return reg


@pytest.fixture
async def gateway(
    bus: EventBus, manager: AdapterManager, registry: MagicMock, tmp_path: Path
) -> AsyncGenerator[ClientGateway]:
    """Provide an initialized ClientGateway with real session/notification parts."""
    session_manager = SessionManager()
    events = EventsFacade(event_bus=bus, adapter_manager=manager)
    notifications = NotificationService(events=events, session_manager=session_manager)
    directory = MemberDirectory(
        members=MembersFacade(agent_registry=registry, tlon_paths=TlonPaths(home=tmp_path)),
        events=events,
    )
    gw = ClientGateway(
        session_manager=session_manager,
        notification_service=notifications,
        member_directory=directory,
        data_dir=tmp_path,
        host="127.0.0.1",
        port=0,
    )
    await gw.initialize()
    yield gw
    await gw.shutdown()


@pytest.fixture
def handler(
    gateway: ClientGateway,
    registry: MagicMock,
    manager: AdapterManager,
    bus: EventBus,
    tmp_path: Path,
) -> RESTAPIHandler:
    return RESTAPIHandler(
        gateway,
        members=MembersFacade(agent_registry=registry, tlon_paths=TlonPaths(home=tmp_path)),
        events=EventsFacade(event_bus=bus, adapter_manager=manager),
    )


@pytest.fixture
async def client(handler: RESTAPIHandler) -> AsyncGenerator[TestClient]:
    """Provide a running in-process HTTP server for the handler."""
    test_client = TestClient(TestServer(handler.create_app()))
    await test_client.start_server()
    yield test_client
    await test_client.close()


def _session_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "username": "alice",
        "client_type": "tui",
        "mode": "event",
        "protocol_version": "2.0.0",
    }
    body.update(overrides)
    return body


async def _create_session(client: TestClient, **overrides: Any) -> dict[str, Any]:
    """POST /sessions against the test server, asserting 201."""
    response = await client.post("/sessions", json=_session_body(**overrides))
    assert response.status == 201
    return await response.json()


async def _wait_for(predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    """Poll until the predicate holds (server-side async effects)."""
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise TimeoutError("condition not met in time")
        await asyncio.sleep(0.02)


async def _read_sse_line(response: Any, timeout: float = 5.0) -> str:
    """Read lines from an SSE response until a non-empty one; return it stripped."""

    async def _next() -> str:
        while True:
            line = await response.content.readline()
            if not line:
                raise AssertionError("SSE stream closed unexpectedly")
            text = line.decode().strip()
            if text:
                return text

    return await asyncio.wait_for(_next(), timeout=timeout)


async def _read_event_frame(response: Any, timeout: float = 5.0) -> dict[str, Any]:
    """Read the next `data:` line and parse its JSON frame."""
    while True:
        line = await _read_sse_line(response, timeout)
        if line.startswith("data: "):
            return json.loads(line[len("data: ") :])


class TestCreateSession:
    """Tests for POST /sessions."""

    async def test_created_returns_201_with_snapshot(
        self, handler: RESTAPIHandler, gateway: ClientGateway
    ):
        response = await handler.handle_create_session(
            StubRequest(_session_body(filters={"event_types": ["mail.*"]}))
        )

        assert response.status == 201
        body = json.loads(response.text)
        assert body["session_id"].startswith("tui-")
        assert body["username"] == "alice"
        assert body["client_type"] == "tui"
        assert body["mode"] == "event"
        assert body["filters"] == {"event_types": ["mail.*"]}
        assert body["connected_at"]
        # Snapshots replace the WS participants/agents push frames
        assert {"id": "xie-yunshu", "name": "谢云舒", "type": "agent"} in body["participants"]
        assert body["agents"] == AGENT_STATUS
        # The session is registered in the gateway
        assert gateway.get_session(body["session_id"]) is not None

    async def test_defaults(self, handler: RESTAPIHandler):
        response = await handler.handle_create_session(
            StubRequest({"client_type": "tui", "protocol_version": "2.0.0"})
        )

        assert response.status == 201
        body = json.loads(response.text)
        assert body["username"] == "guest"
        assert body["mode"] == "chat"
        assert body["filters"] == {}

    @pytest.mark.parametrize("version", [None, "1.0.0", "3.0.0", 42])
    async def test_unsupported_protocol_version(self, handler: RESTAPIHandler, version: Any):
        body = {"client_type": "tui", "username": "alice"}
        if version is not None:
            body["protocol_version"] = version

        response = await handler.handle_create_session(StubRequest(body))

        assert response.status == 400
        payload = json.loads(response.text)
        assert payload["reason"] == "unsupported_protocol_version"
        assert payload["supported_versions"] == ["2.0.0"]

    async def test_invalid_username_rejected(self, handler: RESTAPIHandler):
        for bad in ("", "  ", "../evil", "a/b", "a\\b"):
            response = await handler.handle_create_session(StubRequest(_session_body(username=bad)))
            assert response.status == 400, f"username {bad!r} must be rejected"
            assert json.loads(response.text)["reason"] == "invalid_username"

    async def test_invalid_mode(self, handler: RESTAPIHandler):
        response = await handler.handle_create_session(StubRequest(_session_body(mode="bogus")))

        assert response.status == 400
        assert json.loads(response.text)["reason"] == "invalid_mode"

    async def test_invalid_filters(self, handler: RESTAPIHandler):
        response = await handler.handle_create_session(
            StubRequest(_session_body(filters=["not", "a", "dict"]))
        )

        assert response.status == 400
        assert json.loads(response.text)["reason"] == "invalid_filters"

    async def test_invalid_client_type(self, handler: RESTAPIHandler):
        response = await handler.handle_create_session(StubRequest(_session_body(client_type="  ")))

        assert response.status == 400
        assert json.loads(response.text)["reason"] == "invalid_client_type"

    async def test_invalid_json(self, handler: RESTAPIHandler):
        response = await handler.handle_create_session(
            StubRequest(json.JSONDecodeError("boom", "doc", 0))
        )

        assert response.status == 400
        assert json.loads(response.text)["reason"] == "invalid_json"

    async def test_non_object_body(self, handler: RESTAPIHandler):
        response = await handler.handle_create_session(StubRequest(["not", "a", "dict"]))

        assert response.status == 400
        assert json.loads(response.text)["reason"] == "invalid_json"


class TestUpdateSession:
    """Tests for PATCH /sessions/{id}."""

    async def _create(self, handler: RESTAPIHandler, **overrides: Any) -> str:
        response = await handler.handle_create_session(StubRequest(_session_body(**overrides)))
        assert response.status == 201
        return json.loads(response.text)["session_id"]

    async def test_update_mode_and_filters(self, handler: RESTAPIHandler):
        session_id = await self._create(handler, mode="chat")

        response = await handler.handle_update_session(
            StubRequest({"mode": "event", "filters": {"agent": "xie-*"}}, session_id)
        )

        assert response.status == 200
        body = json.loads(response.text)
        assert body["session_id"] == session_id
        assert body["username"] == "alice"
        assert body["mode"] == "event"
        assert body["filters"] == {"agent": "xie-*"}

    async def test_partial_update_keeps_other_fields(self, handler: RESTAPIHandler):
        session_id = await self._create(handler, filters={"trace_id": "t-1"})

        response = await handler.handle_update_session(StubRequest({"mode": "agent"}, session_id))

        assert response.status == 200
        body = json.loads(response.text)
        assert body["mode"] == "agent"
        assert body["filters"] == {"trace_id": "t-1"}

    async def test_not_found(self, handler: RESTAPIHandler):
        response = await handler.handle_update_session(StubRequest({"mode": "event"}, "nope-0001"))

        assert response.status == 404

    async def test_invalid_mode(self, handler: RESTAPIHandler):
        session_id = await self._create(handler)

        response = await handler.handle_update_session(StubRequest({"mode": "bogus"}, session_id))

        assert response.status == 400
        assert json.loads(response.text)["reason"] == "invalid_mode"

    async def test_invalid_filters(self, handler: RESTAPIHandler):
        session_id = await self._create(handler)

        response = await handler.handle_update_session(StubRequest({"filters": "nope"}, session_id))

        assert response.status == 400
        assert json.loads(response.text)["reason"] == "invalid_filters"


class TestDeleteSession:
    """Tests for DELETE /sessions/{id}."""

    async def test_delete_disconnects_and_is_idempotent(
        self, handler: RESTAPIHandler, gateway: ClientGateway
    ):
        create = await handler.handle_create_session(StubRequest(_session_body()))
        session_id = json.loads(create.text)["session_id"]

        first = await handler.handle_delete_session(StubRequest(session_id=session_id))
        assert first.status == 200
        assert json.loads(first.text)["status"] == "disconnected"
        assert gateway.get_session(session_id) is None

        second = await handler.handle_delete_session(StubRequest(session_id=session_id))
        assert second.status == 200

    async def test_delete_unknown_session_is_idempotent(self, handler: RESTAPIHandler):
        response = await handler.handle_delete_session(StubRequest(session_id="nope-0001"))

        assert response.status == 200


class TestEventsStream:
    """Tests for GET /events (SSE) against a real in-process server."""

    async def test_unknown_session_returns_404(self, client: TestClient):
        response = await client.get("/events", params={"session_id": "nope-0001"})

        assert response.status == 404

    async def test_missing_session_returns_404(self, client: TestClient):
        response = await client.get("/events")

        assert response.status == 404

    async def test_event_frames_keep_the_ws_wire_format(
        self, client: TestClient, bus: EventBus, manager: AdapterManager
    ):
        session = await _create_session(client)
        async with client.get("/events", params={"session_id": session["session_id"]}) as resp:
            assert resp.status == 200
            assert resp.headers["Content-Type"] == "text/event-stream"
            # Stream wiring completes before the 200 headers; the adapter
            # attach is the last step, so it doubles as a readiness signal.
            await _wait_for(lambda: manager.get_adapter("alice") is not None)

            await bus.publish(
                Event(
                    type=EventType.SYSTEM_STARTUP,
                    payload={"agents": 1},
                    source="test",
                    trace_id="trace-1",
                    span_id="span-1",
                )
            )

            frame = await _read_event_frame(resp)
            assert frame["type"] == "event"
            assert frame["event_type"] == "system.startup"
            assert frame["payload"] == {"agents": 1}
            assert frame["source"] == "test"
            assert frame["trace_id"] == "trace-1"
            assert frame["span_id"] == "span-1"

    async def test_session_id_via_header(
        self, client: TestClient, bus: EventBus, manager: AdapterManager
    ):
        session = await _create_session(client)
        async with client.get("/events", headers={"X-Session-Id": session["session_id"]}) as resp:
            assert resp.status == 200
            await _wait_for(lambda: manager.get_adapter("alice") is not None)
            await bus.publish(Event(type=EventType.SYSTEM_SHUTDOWN, payload={}, source="test"))
            frame = await _read_event_frame(resp)
            assert frame["event_type"] == "system.shutdown"

    async def test_heartbeat_comment_lines(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(rest_module, "SSE_HEARTBEAT_SECONDS", 0.05)
        session = await _create_session(client)
        async with client.get("/events", params={"session_id": session["session_id"]}) as resp:
            assert resp.status == 200
            line = await _read_sse_line(resp)
            assert line == ": ping"

    async def test_duplicate_stream_returns_409(self, client: TestClient):
        session = await _create_session(client)
        async with client.get("/events", params={"session_id": session["session_id"]}) as resp:
            assert resp.status == 200

            second = await client.get("/events", params={"session_id": session["session_id"]})
            assert second.status == 409
            assert (await second.json())["reason"] == "stream_active"

    async def test_disconnect_cleans_up(
        self,
        client: TestClient,
        gateway: ClientGateway,
        manager: AdapterManager,
        monkeypatch: pytest.MonkeyPatch,
    ):
        # Short heartbeat: a failed ping write is how the server notices
        # the client is gone.
        monkeypatch.setattr(rest_module, "SSE_HEARTBEAT_SECONDS", 0.05)
        session = await _create_session(client)
        session_id = session["session_id"]

        resp = await client.get("/events", params={"session_id": session_id})
        assert resp.status == 200
        await _wait_for(lambda: manager.get_adapter("alice") is not None)

        resp.close()

        # Session closed, event handler unregistered, adapter detached
        await _wait_for(lambda: gateway.get_session(session_id) is None)
        assert manager.get_adapter("alice") is None
        assert gateway._notifications.get_handler_count() == 0

    async def test_delete_winds_down_active_stream(
        self, client: TestClient, gateway: ClientGateway, manager: AdapterManager
    ):
        session = await _create_session(client)
        session_id = session["session_id"]
        resp = await client.get("/events", params={"session_id": session_id})
        assert resp.status == 200
        await _wait_for(lambda: manager.get_adapter("alice") is not None)

        delete = await client.delete(f"/sessions/{session_id}")
        assert delete.status == 200

        await _wait_for(lambda: gateway.get_session(session_id) is None)
        await _wait_for(lambda: manager.get_adapter("alice") is None)
        resp.close()

    async def test_chat_mode_mail_delivery_is_single_track(
        self, client: TestClient, bus: EventBus, manager: AdapterManager
    ):
        """A chat-mode session gets mail.delivered exactly once (via the
        HumanAdapter fan-out, not the NotificationService), and events
        outside the chat mode patterns are suppressed."""
        session = await _create_session(client, mode="chat")
        async with client.get("/events", params={"session_id": session["session_id"]}) as resp:
            await _wait_for(lambda: manager.get_adapter("alice") is not None)

            mail = Mail.create(
                from_address="xie-yunshu@tlon.internal",
                to=["alice@tlon.internal"],
                content="你好",
            )
            await bus.publish(
                Event(
                    type=EventType.MAIL_DELIVERED,
                    payload={"recipient": "human:alice", "mail": mail},
                    source="mail_delivery",
                )
            )
            # Suppressed by the chat mode patterns (agent.thinking is not
            # in "chat"'s event set)
            await bus.publish(Event(type=EventType.AGENT_THINKING, payload={}, source="xie-yunshu"))
            # Passes the chat mode patterns (system.*)
            await bus.publish(Event(type=EventType.SYSTEM_STARTUP, payload={}, source="test"))

            first = await _read_event_frame(resp)
            second = await _read_event_frame(resp)

        # Exactly one mail.delivered copy (single-track), then the
        # system event — agent.thinking never crossed the stream.
        assert first["event_type"] == "mail.delivered"
        assert first["payload"]["mail"]["id"] == mail.id
        assert first["payload"]["mail"]["content"] == "你好"
        assert second["event_type"] == "system.startup"

    async def test_lifecycle_request_payloads_are_json_objects(
        self, client: TestClient, bus: EventBus, manager: AdapterManager
    ):
        """agent.start_request / agent.stop_request frames carry their
        StartRequest/StopRequest payload as a JSON object, not the
        dataclass repr string."""
        session = await _create_session(client)
        async with client.get("/events", params={"session_id": session["session_id"]}) as resp:
            await _wait_for(lambda: manager.get_adapter("alice") is not None)

            await bus.publish(
                Event(
                    type=EventType.AGENT_START_REQUEST,
                    payload=StartRequest(agent_id="xie-yunshu", reason="wake"),
                    source="agent_registry",
                )
            )
            await bus.publish(
                Event(
                    type=EventType.AGENT_STOP_REQUEST,
                    payload=StopRequest(agent_id="xie-yunshu", reason="drain"),
                    source="adam",
                )
            )

            start = await _read_event_frame(resp)
            stop = await _read_event_frame(resp)

        assert start["event_type"] == "agent.start_request"
        assert start["payload"] == {"agent_id": "xie-yunshu", "reason": "wake"}
        assert stop["event_type"] == "agent.stop_request"
        assert stop["payload"] == {"agent_id": "xie-yunshu", "reason": "drain"}

    async def test_close_sse_streams_winds_down_all_streams(
        self,
        client: TestClient,
        handler: RESTAPIHandler,
        gateway: ClientGateway,
        manager: AdapterManager,
    ):
        first = await _create_session(client)
        second = await _create_session(client, username="bob")
        resp_first = await client.get("/events", params={"session_id": first["session_id"]})
        resp_second = await client.get("/events", params={"session_id": second["session_id"]})
        assert resp_first.status == 200
        assert resp_second.status == 200
        await _wait_for(lambda: manager.get_adapter("alice") is not None)
        await _wait_for(lambda: manager.get_adapter("bob") is not None)

        handler.close_sse_streams()

        # Both loops exit through the normal disconnect path: session
        # closed, adapter detached, queue removed.
        await _wait_for(lambda: gateway.get_session(first["session_id"]) is None)
        await _wait_for(lambda: gateway.get_session(second["session_id"]) is None)
        assert manager.get_adapter("alice") is None
        assert manager.get_adapter("bob") is None
        assert handler._sse_queues == {}
        resp_first.close()
        resp_second.close()


class TestRestServerShutdown:
    """RESTServer.stop() with an active SSE stream (regression: a client
    holding /events open blocked graceful shutdown for ~2x aiohttp's
    shutdown_timeout — Ctrl+C appeared to do nothing)."""

    async def test_stop_ends_active_sse_streams_promptly(
        self,
        gateway: ClientGateway,
        registry: MagicMock,
        bus: EventBus,
        manager: AdapterManager,
        tmp_path: Path,
    ):
        rest = RESTServer(
            gateway,
            members=MembersFacade(agent_registry=registry, tlon_paths=TlonPaths(home=tmp_path)),
            events=EventsFacade(event_bus=bus, adapter_manager=manager),
            host="127.0.0.1",
            port=0,
        )
        await rest.start()
        assert rest._runner is not None
        host, port = rest._runner.addresses[0][:2]
        base = f"http://{host}:{port}"

        async with ClientSession() as http:
            async with http.post(f"{base}/sessions", json=_session_body()) as created:
                assert created.status == 201
                session_id = (await created.json())["session_id"]
            stream = await http.get(f"{base}/events", params={"session_id": session_id})
            assert stream.status == 200
            await _wait_for(lambda: manager.get_adapter("alice") is not None)

            # Previously this blocked ~120s (2x the 60s shutdown_timeout).
            await asyncio.wait_for(rest.stop(), timeout=5.0)

            # The stream ended server-side through the normal disconnect
            # path: session closed and adapter detached before stop()
            # returned.
            assert gateway.get_session(session_id) is None
            assert manager.get_adapter("alice") is None
            await asyncio.wait_for(stream.read(), timeout=5.0)
