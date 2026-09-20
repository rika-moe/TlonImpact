"""Unit tests for the REST POST /messages endpoint (protocol v2, PR-1).

No HTTP server is started: handle_send_message is called directly with a
stub request, against a MailFacade wrapping an AdapterManager rooted at a
tmp dir.

Covers:
- Success: 202 + mail_id, MAIL_NEW published as "human:<username>",
  persistent mailbox created, transient adapter torn down
- Failures: 4xx + {"error", "reason"} for unresolved recipients,
  field validation, invalid username, invalid JSON
"""

import asyncio
import json
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import pytest

from tlon.api.mail import MailFacade
from tlon.app.server.api.rest import RESTAPIHandler
from tlon.base.config import AgentConfig
from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus
from tlon.base.paths import TlonPaths
from tlon.organization.mail.mail import Mail
from tlon.organization.messaging.human_adapter import AdapterManager


class StubRequest:
    """Minimal stand-in for aiohttp.web.Request (json body only)."""

    def __init__(self, body: Any):
        self._body = body

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
    mgr = AdapterManager(
        event_bus=bus,
        agent_info_provider=MockAgentInfoProvider(
            {"xie-yunshu": AgentConfig(id="xie-yunshu", name="谢云舒")}
        ),
        tlon_paths=TlonPaths(home=tmp_path),
    )
    await mgr.initialize()
    yield mgr
    await mgr.shutdown()


@pytest.fixture
def handler(manager: AdapterManager, tmp_path: Path) -> RESTAPIHandler:
    return RESTAPIHandler(
        None,
        mail=MailFacade(adapter_manager=manager, tlon_paths=TlonPaths(home=tmp_path)),
    )


@pytest.fixture
async def collected(bus: EventBus) -> list[Mail]:
    """Collect every MAIL_NEW mail published on the bus."""
    mails: list[Mail] = []

    async def handler(event: Event) -> None:
        if isinstance(event.payload, Mail):
            mails.append(event.payload)

    bus.subscribe(EventType.MAIL_NEW, handler)
    return mails


async def _drain(bus: EventBus) -> None:
    """Wait until all per-subscriber queues have been fully consumed."""
    queues = [
        subscription.queue
        for subscriptions in bus._subscribers.values()
        for subscription in subscriptions
    ]

    async def _join_all() -> None:
        while True:
            await asyncio.gather(*(q.join() for q in queues))
            if all(q.empty() for q in queues):
                return

    await asyncio.wait_for(_join_all(), timeout=5.0)


class TestHandleSendMessage:
    """Tests for POST /messages."""

    async def test_success_returns_202_with_mail_id(
        self,
        handler: RESTAPIHandler,
        collected: list[Mail],
        bus: EventBus,
        tmp_path: Path,
    ):
        response = await handler.handle_send_message(
            StubRequest(
                {
                    "username": "alice",
                    "content": "你好",
                    "to": ["谢云舒"],
                    "cc": ["xie-yunshu"],
                    "subject": "问候",
                }
            )
        )
        await _drain(bus)

        assert response.status == 202
        body = json.loads(response.text)
        assert body == {"mail_id": collected[0].id}

        mail = collected[0]
        assert mail.from_address == "alice@tlon.internal"
        # Names resolved to addresses (dedup is per-field)
        assert mail.to == ["xie-yunshu@tlon.internal"]
        assert mail.cc == ["xie-yunshu@tlon.internal"]
        assert mail.subject == "问候"

        # First contact created the persistent mailbox, and the
        # transient adapter was torn down again
        assert TlonPaths(home=tmp_path).member_mail_dir("human:alice").exists()

    async def test_transient_adapter_is_torn_down(
        self, handler: RESTAPIHandler, manager: AdapterManager
    ):
        response = await handler.handle_send_message(
            StubRequest({"username": "alice", "content": "hi", "to": ["xie-yunshu"]})
        )

        assert response.status == 202
        assert manager.get_adapter("alice") is None

    async def test_attached_human_speaks_through_live_adapter(
        self, handler: RESTAPIHandler, manager: AdapterManager
    ):
        await manager.attach("alice", "session-1", lambda event: None)

        response = await handler.handle_send_message(
            StubRequest({"username": "alice", "content": "hi", "to": ["xie-yunshu"]})
        )

        assert response.status == 202
        # The attached adapter stays alive after the REST send
        assert manager.get_adapter("alice") is not None

    async def test_recipient_unresolved(self, handler: RESTAPIHandler):
        response = await handler.handle_send_message(
            StubRequest({"username": "alice", "content": "hi", "to": ["nobody"]})
        )

        assert response.status == 400
        body = json.loads(response.text)
        assert body["reason"] == "recipient_unresolved"
        assert body["error"]

    async def test_missing_content(self, handler: RESTAPIHandler):
        response = await handler.handle_send_message(
            StubRequest({"username": "alice", "to": ["xie-yunshu"]})
        )

        assert response.status == 400
        assert json.loads(response.text)["reason"] == "empty_content"

    async def test_invalid_to(self, handler: RESTAPIHandler):
        response = await handler.handle_send_message(
            StubRequest({"username": "alice", "content": "hi", "to": "xie-yunshu"})
        )

        assert response.status == 400
        assert json.loads(response.text)["reason"] == "invalid_to"

    async def test_invalid_thread_id(self, handler: RESTAPIHandler):
        response = await handler.handle_send_message(
            StubRequest(
                {
                    "username": "alice",
                    "content": "hi",
                    "to": ["xie-yunshu"],
                    "thread_id": 42,
                }
            )
        )

        assert response.status == 400
        assert json.loads(response.text)["reason"] == "invalid_thread_id"

    async def test_invalid_username_rejected(self, handler: RESTAPIHandler):
        for bad in ("", "  ", "../evil", "a/b", "a\\b"):
            response = await handler.handle_send_message(
                StubRequest({"username": bad, "content": "hi", "to": ["xie-yunshu"]})
            )
            assert response.status == 400, f"username {bad!r} must be rejected"
            assert json.loads(response.text)["reason"] == "invalid_username"

    async def test_invalid_json(self, handler: RESTAPIHandler):
        response = await handler.handle_send_message(
            StubRequest(json.JSONDecodeError("boom", "doc", 0))
        )

        assert response.status == 400
        assert json.loads(response.text)["reason"] == "invalid_json"

    async def test_non_object_body(self, handler: RESTAPIHandler):
        response = await handler.handle_send_message(StubRequest(["not", "a", "dict"]))

        assert response.status == 400
        assert json.loads(response.text)["reason"] == "invalid_json"

    async def test_messaging_unavailable_returns_503(self):
        handler = RESTAPIHandler(None, mail=None)

        response = await handler.handle_send_message(
            StubRequest({"username": "alice", "content": "hi", "to": ["xie-yunshu"]})
        )

        assert response.status == 503
