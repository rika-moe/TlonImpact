"""Unit tests for Tlon SDK.

Tests cover:
- BaseClient connection/disconnection/authentication
- Adapter filter declaration logic (get_effective_filters) — the actual
  event matching is server-side and tested in test_notification_service.py
- Protocol version handling
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tlon.app.clients.sdk.adapter import TerminalAdapter
from tlon.app.clients.sdk.base_client import BaseClient, ConnectionState
from tlon.app.clients.sdk.client import TlonClient
from tlon.app.clients.sdk.exceptions import (
    AuthenticationError,
    ConnectionError,
    ProtocolError,
)


class TestBaseClient:
    """Tests for BaseClient class."""

    @pytest.fixture
    def client(self):
        """Create a BaseClient instance for testing."""
        return BaseClient(
            ws_url="ws://localhost:8888/ws",
            rest_url="http://localhost:8080",
            auto_reconnect=False,
        )

    @pytest.fixture
    def mock_ws(self):
        """Create a mock WebSocket response."""
        ws = AsyncMock()
        ws.closed = True  # Start as closed to prevent infinite receive loop
        ws.close = AsyncMock()
        return ws

    @pytest.fixture
    def mock_session(self, mock_ws):
        """Create a mock aiohttp ClientSession."""
        session = AsyncMock()
        session.ws_connect = AsyncMock(return_value=mock_ws)
        session.close = AsyncMock()
        return session

    def test_init(self, client):
        """Test BaseClient initialization."""
        assert client.ws_url == "ws://localhost:8888/ws"
        assert client.rest_url == "http://localhost:8080"
        assert client.state == ConnectionState.DISCONNECTED
        assert not client.is_connected
        assert not client.is_authenticated
        assert client.session_id is None

    def test_derive_rest_url(self, client):
        """Test REST URL derivation from WebSocket URL."""
        # Test ws:// to http://
        assert client._derive_rest_url("ws://localhost:8888/ws") == "http://localhost:8888"
        # Test wss:// to https://
        assert client._derive_rest_url("wss://example.com/ws") == "https://example.com"
        # Test with port
        assert client._derive_rest_url("ws://host:9999/ws") == "http://host:9999"

    @pytest.mark.asyncio
    async def test_connect_success(self, client, mock_session, mock_ws):
        """Test successful connection."""
        with patch("aiohttp.ClientSession", return_value=mock_session):
            result = await client.connect()

        assert result is True
        assert client.state == ConnectionState.CONNECTED
        assert client.is_connected
        mock_session.ws_connect.assert_called_once_with("ws://localhost:8888/ws")

    @pytest.mark.asyncio
    async def test_connect_already_connected(self, client, mock_session, mock_ws):
        """Test connect when already connected."""
        with patch("aiohttp.ClientSession", return_value=mock_session):
            await client.connect()
            result = await client.connect()

        assert result is True
        # Should not create new connection
        mock_session.ws_connect.assert_called_once()

    @pytest.mark.asyncio
    async def test_connect_failure(self, client):
        """Test connection failure."""
        with patch("aiohttp.ClientSession") as mock:
            mock.return_value.ws_connect = AsyncMock(side_effect=Exception("Connection refused"))
            mock.return_value.close = AsyncMock()

            with pytest.raises(ConnectionError):
                await client.connect()

        assert client.state == ConnectionState.ERROR

    @pytest.mark.asyncio
    async def test_disconnect(self, client, mock_session, mock_ws):
        """Test disconnection."""
        with patch("aiohttp.ClientSession", return_value=mock_session):
            await client.connect()
            await client.disconnect()

        assert client.state == ConnectionState.DISCONNECTED
        assert not client.is_connected
        mock_ws.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_authenticate_success(self, client, mock_session, mock_ws):
        """Test successful authentication."""
        auth_response = {
            "type": "auth_success",
            "session_id": "test-session-123",
            "protocol_version": "1.0.0",
            "server_version": "0.2.0",
            "capabilities": ["trace", "filter"],
        }

        # Mock the auth response waiting
        async def mock_wait_for_auth():
            client._auth_response = auth_response
            return auth_response

        with patch("aiohttp.ClientSession", return_value=mock_session):
            await client.connect()
            with patch.object(client, "_wait_for_auth_response", mock_wait_for_auth):
                result = await client.authenticate("tui", capabilities=["trace"])

        assert result is True
        assert client.is_authenticated
        assert client.session_id == "test-session-123"
        assert client.protocol_version == "1.0.0"
        assert "trace" in client.server_capabilities

    @pytest.mark.asyncio
    async def test_authenticate_not_connected(self, client):
        """Test authentication without connection."""
        with pytest.raises(ConnectionError):
            await client.authenticate("tui")

    @pytest.mark.asyncio
    async def test_authenticate_version_mismatch(self, client, mock_session, mock_ws):
        """Test authentication with version mismatch."""
        auth_response = {
            "type": "auth_failed",
            "error_code": "VERSION_MISMATCH",
            "message": "Protocol version 1.0.0 not supported",
            "supported_versions": ["1.1.0"],
        }

        async def mock_wait_for_auth():
            client._auth_response = auth_response
            return auth_response

        with patch("aiohttp.ClientSession", return_value=mock_session):
            await client.connect()
            with (
                patch.object(client, "_wait_for_auth_response", mock_wait_for_auth),
                pytest.raises(ProtocolError) as exc_info,
            ):
                await client.authenticate("tui")

        assert exc_info.value.error_code == "VERSION_MISMATCH"
        assert "1.1.0" in exc_info.value.server_versions

    @pytest.mark.asyncio
    async def test_authenticate_timeout(self, client, mock_session, mock_ws):
        """Test authentication timeout."""

        async def mock_wait_for_auth():
            await asyncio.sleep(100)  # Simulate timeout

        with patch("aiohttp.ClientSession", return_value=mock_session):
            await client.connect()
            with (
                patch.object(client, "_wait_for_auth_response", mock_wait_for_auth),
                pytest.raises(AuthenticationError) as exc_info,
            ):
                await client.authenticate("tui", timeout=0.1)

        assert "timeout" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_send_message(self, client):
        """Test sending a message."""
        # Mock the internal state without going through connect
        client._session = AsyncMock()
        client._ws = AsyncMock()
        client._ws.closed = False
        client._ws.send_str = AsyncMock()
        client._state = ConnectionState.AUTHENTICATED

        result = await client.send({"type": "message", "content": "Hello"})

        assert result is True
        client._ws.send_str.assert_called_once()
        # Verify JSON was sent
        sent_data = json.loads(client._ws.send_str.call_args[0][0])
        assert sent_data["type"] == "message"
        assert sent_data["content"] == "Hello"
        assert "msg_id" in sent_data
        assert "timestamp" in sent_data

    @pytest.mark.asyncio
    async def test_send_not_authenticated(self, client):
        """Test sending without authentication."""
        # Mock connected but not authenticated
        client._session = AsyncMock()
        client._ws = AsyncMock()
        client._state = ConnectionState.CONNECTED  # Not AUTHENTICATED

        result = await client.send({"type": "message", "content": "Hello"})

        assert result is False

    @pytest.mark.asyncio
    async def test_event_handler(self, client, mock_session, mock_ws):
        """Test event handler registration and dispatch."""
        handler = MagicMock()
        client.on_event(handler)

        event = {"type": "message.new", "payload": {"content": "test"}}

        await client._dispatch_event(event)

        handler.assert_called_once_with(event)

    @pytest.mark.asyncio
    async def test_async_event_handler(self, client, mock_session, mock_ws):
        """Test async event handler."""
        async_handler = AsyncMock()
        client.on_event(async_handler)

        event = {"type": "message.new", "payload": {"content": "test"}}

        await client._dispatch_event(event)

        # Async handler should be called
        async_handler.assert_called_once_with(event)

    @pytest.mark.asyncio
    async def test_state_change_handler(self, client, mock_session, mock_ws):
        """Test state change handler."""
        handler = MagicMock()
        client.on_state_change(handler)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            await client.connect()

        # Handler should have been called with CONNECTED state
        handler.assert_called()
        assert any(call.args[0] == ConnectionState.CONNECTED for call in handler.call_args_list)

    @pytest.mark.asyncio
    async def test_authenticate_response_arriving_before_wait(self, client, mock_session, mock_ws):
        """Regression: an auth response arriving before the wait starts must not be lost.

        The auth event must be created before the auth message is sent,
        otherwise a fast response is dropped and authentication times out.
        """
        auth_response = {
            "type": "auth_success",
            "session_id": "early-session",
            "protocol_version": "1.0.0",
            "server_version": "0.2.0",
            "capabilities": ["trace"],
        }

        async def fake_send_raw(data):
            # Simulate the server responding during the send itself,
            # i.e. before authenticate() starts waiting for the response.
            if data.get("type") == "auth":
                await client._handle_message(json.dumps(auth_response))
            return True

        with patch("aiohttp.ClientSession", return_value=mock_session):
            await client.connect()
            with patch.object(client, "_send_raw", fake_send_raw):
                result = await client.authenticate("tui", capabilities=["trace"], timeout=1.0)

        assert result is True
        assert client.is_authenticated
        assert client.session_id == "early-session"

    @pytest.mark.asyncio
    async def test_reconnect_reauthenticates(self):
        """Regression: auto-reconnect must re-authenticate so send() works again."""
        import aiohttp

        client = BaseClient(
            ws_url="ws://localhost:8888/ws",
            rest_url="http://localhost:8080",
            auto_reconnect=True,
            reconnect_delay=0.01,
        )

        # Each (re)connection gets a fresh mock ws whose receive() blocks on a
        # queue, so we control exactly when the connection drops.
        queue: asyncio.Queue = asyncio.Queue()

        def make_ws():
            ws = AsyncMock()
            ws.closed = False
            ws.receive = AsyncMock(side_effect=queue.get)
            ws.send_str = AsyncMock()
            ws.close = AsyncMock()
            return ws

        session = AsyncMock()
        session.ws_connect = AsyncMock(side_effect=lambda url: make_ws())
        session.close = AsyncMock()

        auth_response = {
            "type": "auth_success",
            "session_id": "reconnect-session",
            "protocol_version": "1.0.0",
            "server_version": "0.2.0",
            "capabilities": ["trace"],
        }

        async def mock_wait_for_auth():
            return auth_response

        try:
            with patch("aiohttp.ClientSession", return_value=session):
                await client.connect()
                with patch.object(client, "_wait_for_auth_response", mock_wait_for_auth):
                    await client.authenticate("tui", capabilities=["trace"])
                    assert client.is_authenticated

                    # Simulate the server closing the connection
                    queue.put_nowait(MagicMock(type=aiohttp.WSMsgType.CLOSED))

                    # Wait for auto-reconnect + re-authentication to complete
                    for _ in range(200):
                        await asyncio.sleep(0.01)
                        if client.is_authenticated:
                            break

                    assert client.is_authenticated
                    assert session.ws_connect.call_count == 2

                    # send() must work on the reconnected session
                    result = await client.send({"type": "message", "content": "hi"})
                    assert result is True
        finally:
            await client.disconnect()

    @pytest.mark.asyncio
    async def test_reconnect_notifies_on_reconnect(self):
        """on_reconnect handlers fire after auto-reconnect + re-authentication."""
        import aiohttp

        client = BaseClient(
            ws_url="ws://localhost:8888/ws",
            rest_url="http://localhost:8080",
            auto_reconnect=True,
            reconnect_delay=0.01,
        )

        queue: asyncio.Queue = asyncio.Queue()

        def make_ws():
            ws = AsyncMock()
            ws.closed = False
            ws.receive = AsyncMock(side_effect=queue.get)
            ws.send_str = AsyncMock()
            ws.close = AsyncMock()
            return ws

        session = AsyncMock()
        session.ws_connect = AsyncMock(side_effect=lambda url: make_ws())
        session.close = AsyncMock()

        auth_response = {
            "type": "auth_success",
            "session_id": "reconnect-session",
            "protocol_version": "1.0.0",
            "server_version": "0.2.0",
            "capabilities": ["trace"],
        }

        async def mock_wait_for_auth():
            return auth_response

        on_reconnect = MagicMock()
        client.on_reconnect(on_reconnect)

        try:
            with patch("aiohttp.ClientSession", return_value=session):
                await client.connect()
                with patch.object(client, "_wait_for_auth_response", mock_wait_for_auth):
                    await client.authenticate("tui", capabilities=["trace"])
                    assert client.is_authenticated

                    # Initial connect must not trigger the reconnect handlers
                    on_reconnect.assert_not_called()

                    # Simulate the server closing the connection
                    queue.put_nowait(MagicMock(type=aiohttp.WSMsgType.CLOSED))

                    # Wait for auto-reconnect + re-authentication to complete
                    for _ in range(200):
                        await asyncio.sleep(0.01)
                        if client.is_authenticated:
                            break

                    assert client.is_authenticated
                    on_reconnect.assert_called_once_with()
        finally:
            await client.disconnect()


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
        adapter.set_event_filters({"event_types": ["message.new", "agent.response"]})
        assert adapter.get_effective_filters() == {
            "event_types": ["message.new", "agent.response"]
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
        adapter.set_event_filters({"event_types": ["message.new"]})

        adapter.clear_filters()

        assert adapter._trace_filter is None
        assert adapter._event_filters is None
        assert adapter.get_effective_filters() == {}

    def test_get_auth_metadata(self, adapter):
        """Test auth metadata generation."""
        metadata = adapter.get_auth_metadata()

        assert "client_version" in metadata
        assert "platform" in metadata


class TestTlonClient:
    """Tests for TlonClient class."""

    @pytest.fixture
    def client(self):
        """Create a TlonClient instance."""
        return TlonClient(
            ws_url="ws://localhost:8888/ws",
            adapter=TerminalAdapter(),
        )

    def test_init(self, client):
        """Test TlonClient initialization."""
        # ws_url is stored in _base
        assert client._base.ws_url == "ws://localhost:8888/ws"
        assert isinstance(client.adapter, TerminalAdapter)

    def test_properties(self, client):
        """Test TlonClient properties."""
        # Initially not connected
        assert not client.is_connected
        assert not client.is_authenticated
        assert client.session_id is None

    @pytest.mark.asyncio
    async def test_context_manager(self, client):
        """Test async context manager."""
        with (
            patch.object(client, "connect", AsyncMock(return_value=True)) as mock_connect,
            patch.object(client, "disconnect", AsyncMock()) as mock_disconnect,
        ):
            async with client as c:
                assert c is client

            mock_connect.assert_called_once()
            mock_disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_message(self, client):
        """Test send_message method."""
        client._base = AsyncMock()
        client._base.send = AsyncMock(return_value=True)

        result = await client.send_message("Hello", mentions=["agent-1"])

        assert result is True
        # Should call _base.send with the message dict
        client._base.send.assert_called_once()
        call_args = client._base.send.call_args[0][0]
        assert call_args["type"] == "message"
        assert call_args["content"] == "Hello"
        assert call_args["mentions"] == ["agent-1"]

    @pytest.mark.asyncio
    async def test_set_trace_filter(self, client):
        """Test set_trace_filter method."""
        client._base = AsyncMock()
        client._base.send = AsyncMock(return_value=True)

        result = await client.set_trace_filter("trace-123")

        assert result is True
        assert client.adapter._trace_filter == "trace-123"
        # Should send filter message to server
        client._base.send.assert_called_once()
        call_args = client._base.send.call_args[0][0]
        assert call_args["type"] == "filter"
        assert call_args["filters"]["trace_id"] == "trace-123"

    @pytest.mark.asyncio
    async def test_set_trace_filter_clear(self, client):
        """Test clearing trace filter."""
        client._base = AsyncMock()
        client._base.send = AsyncMock(return_value=True)

        # First set a filter
        await client.set_trace_filter("trace-123")
        # Then clear it
        result = await client.set_trace_filter(None)

        assert result is True
        assert client.adapter._trace_filter is None

    @pytest.mark.asyncio
    async def test_on_event_dispatches_without_local_filtering(self, client):
        """Events are filtered server-side; _on_event dispatches everything."""
        handler = MagicMock()
        client.on_event(handler)

        # Simulate receiving a message event
        message_event = {
            "type": "message.new",
            "payload": {"content": "Hello"},
        }
        client._on_event(message_event)
        handler.assert_called_once_with(message_event)

        handler.reset_mock()

        # Agent events are dispatched too (server already decided to send them)
        agent_event = {
            "type": "agent.thinking",
            "payload": {"agent_id": "dev-001"},
        }
        client._on_event(agent_event)
        handler.assert_called_once_with(agent_event)

    @pytest.mark.asyncio
    async def test_authenticate_declares_adapter_filters(self, client):
        """authenticate() passes the adapter's effective filters to the server."""
        client._base = AsyncMock()
        client._base.authenticate = AsyncMock(return_value=True)

        result = await client.authenticate()

        assert result is True
        call_kwargs = client._base.authenticate.call_args.kwargs
        assert call_kwargs["client_type"] == "tui"
        assert call_kwargs["filters"] == client.adapter.get_effective_filters()

    @pytest.mark.asyncio
    async def test_set_mode_syncs_mode_and_filters(self, client):
        """set_mode sends both the mode and the re-synced effective filters."""
        client._base = AsyncMock()
        client._base.send = AsyncMock(return_value=True)

        result = await client.set_mode("event")

        assert result is True
        assert client.adapter.mode == "event"
        assert client._base.send.call_count == 2

        mode_msg = client._base.send.call_args_list[0][0][0]
        assert mode_msg["type"] == "mode"
        assert mode_msg["mode"] == "event"

        filter_msg = client._base.send.call_args_list[1][0][0]
        assert filter_msg["type"] == "filter"
        assert filter_msg["filters"] == client.adapter.get_effective_filters()

    @pytest.mark.asyncio
    async def test_rest_api_methods(self, client):
        """Test REST API wrapper methods."""
        client._base = AsyncMock()
        client._base.rest_get = AsyncMock(
            side_effect=[
                {"agents": [{"id": "agent-1"}]},
                {"messages": [{"content": "test"}]},
                {"trace_id": "t-1"},
            ]
        )

        agents = await client.get_agents()
        assert len(agents) == 1
        assert agents[0]["id"] == "agent-1"

        history = await client.get_history(limit=10)
        assert len(history) == 1

        trace = await client.get_trace("t-1")
        assert trace["trace_id"] == "t-1"
