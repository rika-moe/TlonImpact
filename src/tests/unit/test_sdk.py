"""Unit tests for Tlon SDK.

Tests cover:
- BaseClient connection/disconnection/authentication
- TerminalAdapter event filtering logic
- Protocol version handling
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tlon.app.clients.sdk import (
    BaseClient,
    ConnectionState,
    TerminalAdapter,
    TlonClient,
    WebAdapter,
)
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

    def test_should_receive_event_chat_mode(self, adapter):
        """Test event filtering in chat mode."""
        adapter.set_mode("chat")

        # Should receive message events
        assert adapter.should_receive_event("message.new", {"content": "test"})
        assert adapter.should_receive_event("message.delivered", {})

        # Should not receive other events
        assert not adapter.should_receive_event("agent.thinking", {})
        assert not adapter.should_receive_event("tool.execute", {})

    def test_should_receive_event_event_mode(self, adapter):
        """Test event filtering in event mode."""
        adapter.set_mode("event")

        # Should receive all events
        assert adapter.should_receive_event("message.new", {})
        assert adapter.should_receive_event("agent.thinking", {})
        assert adapter.should_receive_event("tool.execute", {})
        assert adapter.should_receive_event("system.startup", {})

    def test_should_receive_event_agent_mode(self, adapter):
        """Test event filtering in agent mode."""
        adapter.set_mode("agent")

        # Should receive agent events
        assert adapter.should_receive_event("agent.thinking", {})
        assert adapter.should_receive_event("agent.response", {})
        assert adapter.should_receive_event("agent.started", {})

        # Should not receive other events
        assert not adapter.should_receive_event("message.new", {})
        assert not adapter.should_receive_event("tool.execute", {})

    def test_should_receive_event_with_trace_filter(self, adapter):
        """Test event filtering with trace_id filter."""
        adapter.set_mode("event")
        adapter.set_trace_filter("trace-abc-123")

        # Should receive events with matching trace_id
        assert adapter.should_receive_event("agent.thinking", {"trace_id": "trace-abc-123"})

        # Should not receive events with different trace_id
        assert not adapter.should_receive_event("agent.thinking", {"trace_id": "trace-other"})
        assert not adapter.should_receive_event("agent.thinking", {})

    def test_should_receive_event_with_event_type_filter(self, adapter):
        """Test event filtering with event type filter."""
        adapter.set_mode("event")  # Set mode to event to bypass mode filtering
        adapter.set_event_filters({"event_types": ["message.new", "agent.response"]})

        # Should receive allowed event types
        assert adapter.should_receive_event("message.new", {})
        assert adapter.should_receive_event("agent.response", {})

        # Should not receive other event types
        assert not adapter.should_receive_event("agent.thinking", {})
        assert not adapter.should_receive_event("tool.execute", {})

    def test_should_receive_event_high_frequency_exclusion(self, adapter):
        """Test high-frequency event exclusion."""
        adapter.set_mode("event")
        adapter.set_exclude_high_frequency(True)

        # Should not receive high-frequency events
        assert not adapter.should_receive_event("agent.llm_request", {})
        assert not adapter.should_receive_event("agent.llm_response", {})

        # Should receive other events
        assert adapter.should_receive_event("message.new", {})
        assert adapter.should_receive_event("agent.thinking", {})

    def test_clear_filters(self, adapter):
        """Test clearing filters."""
        adapter.set_trace_filter("trace-123")
        adapter.set_event_filters({"event_types": ["message.new"]})

        adapter.clear_filters()

        assert adapter._trace_filter is None
        assert adapter._event_filters is None

    def test_get_auth_metadata(self, adapter):
        """Test auth metadata generation."""
        metadata = adapter.get_auth_metadata()

        assert "client_version" in metadata
        assert "platform" in metadata


class TestWebAdapter:
    """Tests for WebAdapter class."""

    @pytest.fixture
    def adapter(self):
        """Create a WebAdapter instance."""
        return WebAdapter()

    def test_init(self, adapter):
        """Test WebAdapter initialization."""
        assert adapter.client_type == "web"

    def test_should_receive_event_default_filtering(self, adapter):
        """Test default event filtering for web clients."""
        # Should receive message events
        assert adapter.should_receive_event("message.new", {})

        # Should not receive high-frequency events by default
        assert not adapter.should_receive_event("agent.llm_request", {})
        assert not adapter.should_receive_event("agent.llm_response", {})
        assert not adapter.should_receive_event("agent.thinking_start", {})

    def test_should_receive_event_in_trace_mode(self, adapter):
        """Test event filtering in trace mode."""
        adapter.set_trace_mode(True)

        # Should receive all events in trace mode
        assert adapter.should_receive_event("agent.llm_request", {})
        assert adapter.should_receive_event("agent.llm_response", {})
        assert adapter.should_receive_event("message.new", {})

    def test_should_receive_event_with_trace_filter(self, adapter):
        """Test event filtering with trace filter."""
        adapter.set_trace_filter("trace-xyz-789")

        # Should receive events with matching trace_id even if high-frequency
        assert adapter.should_receive_event("agent.llm_request", {"trace_id": "trace-xyz-789"})

        # Should not receive events with different trace_id
        assert not adapter.should_receive_event("agent.llm_request", {"trace_id": "trace-other"})


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
    async def test_on_event_with_adapter_filtering(self, client):
        """Test that events are filtered through adapter."""
        handler = MagicMock()
        client.on_event(handler)

        # Set adapter to chat mode (only message events)
        client.adapter.set_mode("chat")

        # Simulate receiving a message event
        message_event = {
            "type": "message.new",
            "payload": {"content": "Hello"},
        }
        client._on_event(message_event)

        # Handler should be called
        handler.assert_called_once_with(message_event)

        # Reset mock
        handler.reset_mock()

        # Simulate receiving an agent event (should be filtered out)
        agent_event = {
            "type": "agent.thinking",
            "payload": {"agent_id": "dev-001"},
        }
        client._on_event(agent_event)

        # Handler should not be called
        handler.assert_not_called()

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
