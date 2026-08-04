"""Unit tests for the WebSocket auth flow (WebSocketHandler._authenticate).

Covers the human adapter attach: the session's event handler is passed
to AdapterManager.attach as the mail delivery sink (single-track human
mail delivery, docs/design/members.md 阶段 B).
"""

import json
from unittest.mock import AsyncMock, MagicMock

from aiohttp import WSMsgType

from tlon.app.server.api.websocket import WebSocketHandler
from tlon.app.server.core.session_manager import ClientSession


def _make_ws(auth_payload: dict) -> MagicMock:
    """Create a mock WebSocket that yields one auth message."""
    msg = MagicMock()
    msg.type = WSMsgType.TEXT
    msg.data = json.dumps(auth_payload)

    ws = MagicMock()
    ws.closed = False
    ws.receive = AsyncMock(return_value=msg)
    ws.send_str = AsyncMock()
    return ws


def _make_session(session_id: str = "tui-0001") -> ClientSession:
    """Create the session the client gateway would return."""
    return ClientSession(
        id=session_id,
        client_type="tui",
        connected_at="2026-01-01T00:00:00",
        mode="chat",
        filters={},
        metadata={"username": "alice"},
    )


class TestAuthenticateAttach:
    """Tests for the adapter attach during authentication."""

    async def test_attach_receives_event_handler_as_sink(self):
        """adapter_manager.attach is called with (username, session_id, sink)."""
        session = _make_session()

        client_gateway = MagicMock()
        client_gateway.connect_client = AsyncMock(return_value=session)
        adapter_manager = MagicMock()
        adapter_manager.attach = AsyncMock()

        handler = WebSocketHandler(client_gateway, MagicMock(), adapter_manager)
        ws = _make_ws({"type": "auth", "client_type": "tui", "username": "alice"})

        result = await handler._authenticate(ws)

        assert result is not None
        adapter_manager.attach.assert_awaited_once()
        username, session_id, sink = adapter_manager.attach.await_args.args
        assert username == "alice"
        assert session_id == session.id
        assert callable(sink)

    async def test_attach_sink_writes_ws_frames(self):
        """The sink passed to attach is the session's event handler."""
        session = _make_session()

        client_gateway = MagicMock()
        client_gateway.connect_client = AsyncMock(return_value=session)
        adapter_manager = MagicMock()
        adapter_manager.attach = AsyncMock()

        handler = WebSocketHandler(client_gateway, MagicMock(), adapter_manager)
        ws = _make_ws({"type": "auth", "client_type": "tui", "username": "alice"})

        await handler._authenticate(ws)

        sink = adapter_manager.attach.await_args.args[2]
        # The sink is the same callback registered with the client gateway
        # (it writes WS frames via _send_event)
        assert sink is client_gateway.connect_client.await_args.kwargs["event_handler"]
