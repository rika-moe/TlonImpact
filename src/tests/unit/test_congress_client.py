"""Unit tests for CongressWebSocketClient reconnection behavior.

Congress delegates reconnection to the SDK's auto-reconnect; these tests
cover the Congress-side recovery logic that runs after the SDK reconnects.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tlon.app.clients.sdk.base_client import ConnectionState
from tlon.app.clients.tui.congress.client import ClientConnectionState, CongressWebSocketClient


class TestCongressReconnection:
    """Tests for Congress client reconnection configuration and recovery."""

    @pytest.fixture
    def client(self):
        """Create a CongressWebSocketClient with a mocked SDK client."""
        client = CongressWebSocketClient(
            ws_url="ws://localhost:8888/ws",
            rest_url="http://localhost:8080",
        )
        client._sdk = MagicMock()
        client._sdk.set_mode = AsyncMock(return_value=True)
        client._sdk.get_agents = AsyncMock(return_value=[])
        client._sdk.get_history = AsyncMock(return_value=[])
        return client

    def test_sdk_auto_reconnect_enabled(self):
        """The SDK client is configured to handle reconnection itself."""
        with patch("tlon.app.clients.tui.congress.client.TlonClient") as mock_tlon:
            CongressWebSocketClient(ws_url="ws://localhost:8888/ws")

        kwargs = mock_tlon.call_args.kwargs
        assert kwargs["auto_reconnect"] is True
        assert kwargs["reconnect_delay"] == CongressWebSocketClient.RECONNECT_DELAY
        assert kwargs["max_reconnect_attempts"] == CongressWebSocketClient.MAX_RECONNECT_ATTEMPTS
        # Reconnect recovery hook is registered on the SDK client
        mock_tlon.return_value.on_reconnect.assert_called_once()

    def test_sdk_auto_reconnect_disabled(self):
        """auto_reconnect=False is propagated to the SDK client."""
        with patch("tlon.app.clients.tui.congress.client.TlonClient") as mock_tlon:
            CongressWebSocketClient(ws_url="ws://localhost:8888/ws", auto_reconnect=False)

        assert mock_tlon.call_args.kwargs["auto_reconnect"] is False

    @pytest.mark.asyncio
    async def test_on_reconnected_restores_session(self, client):
        """Reconnect recovery re-declares chat mode and reloads agents/history."""
        client._sdk.get_agents.return_value = [
            {"id": "dev-1", "name": "Dev", "role": "dev", "state": "idle"}
        ]
        client._sdk.get_history.return_value = [
            {"id": "m-1", "content": "hello", "sender_type": "agent", "sender_name": "Dev"}
        ]

        await client._on_reconnected()

        client._sdk.set_mode.assert_awaited_once_with("chat")
        client._sdk.get_agents.assert_awaited_once()
        client._sdk.get_history.assert_awaited_once()
        assert "dev-1" in client.state.agents
        assert [m.id for m in client.state.messages] == ["m-1"]

    def test_disconnected_suppressed_while_sdk_reconnects(self, client):
        """A dropped authenticated connection is not surfaced as DISCONNECTED.

        The SDK auto-reconnect engages immediately and reports RECONNECTING
        next, so Congress skips the intermediate DISCONNECTED state.
        """
        client._connection_state = ClientConnectionState.AUTHENTICATED

        client._on_sdk_state_change(ConnectionState.DISCONNECTED)

        assert client.connection_state == ClientConnectionState.AUTHENTICATED

        client._on_sdk_state_change(ConnectionState.RECONNECTING)
        assert client.connection_state == ClientConnectionState.RECONNECTING

    def test_disconnected_mapped_when_auto_reconnect_off(self):
        """Without auto-reconnect, a dropped connection surfaces as DISCONNECTED."""
        client = CongressWebSocketClient(ws_url="ws://localhost:8888/ws", auto_reconnect=False)
        client._connection_state = ClientConnectionState.AUTHENTICATED

        client._on_sdk_state_change(ConnectionState.DISCONNECTED)

        assert client.connection_state == ClientConnectionState.DISCONNECTED
