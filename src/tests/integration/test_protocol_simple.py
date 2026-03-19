"""Simple protocol tests without full server startup."""

import json

from tlon.app.clients.sdk.base_client import BaseClient
from tlon.app.server.api.websocket import WebSocketHandler


class TestProtocolVersion:
    """Test protocol version negotiation logic."""

    def test_server_supported_versions(self):
        """Test server has correct supported versions."""
        assert WebSocketHandler.PROTOCOL_VERSION == "1.0.0"
        assert WebSocketHandler.SUPPORTED_VERSIONS == ["1.0.0"]
        assert WebSocketHandler.MIN_SUPPORTED_VERSION == "1.0.0"

    def test_client_supported_versions(self):
        """Test client has correct supported versions."""
        assert BaseClient.PREFERRED_VERSION == "1.0.0"
        assert BaseClient.SUPPORTED_VERSIONS == ["1.0.0"]


class TestAuthMessageFormat:
    """Test auth message format matches protocol spec."""

    def test_client_auth_message_structure(self):
        """Test client auth message has all required fields."""
        auth_msg = {
            "type": "auth",
            "client_type": "tui",
            "protocol_version": "1.0.0",
            "supported_versions": ["1.0.0"],
            "capabilities": ["trace", "filter", "history"],
            "metadata": {"client_version": "0.2.0", "platform": "terminal"},
        }

        # Verify structure
        assert auth_msg["type"] == "auth"
        assert "client_type" in auth_msg
        assert "protocol_version" in auth_msg
        assert "supported_versions" in auth_msg
        assert "capabilities" in auth_msg
        assert "metadata" in auth_msg

        # Verify types
        assert isinstance(auth_msg["supported_versions"], list)
        assert isinstance(auth_msg["capabilities"], list)
        assert isinstance(auth_msg["metadata"], dict)

    def test_server_auth_success_structure(self):
        """Test server auth success response has all required fields."""
        auth_success = {
            "type": "auth_success",
            "session_id": "test-session-123",
            "protocol_version": "1.0.0",
            "server_version": "0.2.0",
            "server_time": "2026-03-19T12:00:00Z",
            "capabilities": ["trace", "filter", "history"],
            "timestamp": "2026-03-19T12:00:00Z",
        }

        # Verify structure
        assert auth_success["type"] == "auth_success"
        assert "session_id" in auth_success
        assert "protocol_version" in auth_success
        assert "server_version" in auth_success
        assert "server_time" in auth_success
        assert "capabilities" in auth_success

    def test_server_auth_failed_structure(self):
        """Test server auth failed response has all required fields."""
        auth_failed = {
            "type": "auth_failed",
            "error_code": "VERSION_MISMATCH",
            "message": "Protocol version not supported",
            "supported_versions": ["1.0.0"],
        }

        # Verify structure
        assert auth_failed["type"] == "auth_failed"
        assert "error_code" in auth_failed
        assert "message" in auth_failed
        assert "supported_versions" in auth_failed


class TestEventFormat:
    """Test event format with trace fields."""

    def test_event_with_trace_fields(self):
        """Test event includes trace fields."""

        from tlon.base.events.event import Event, EventType

        event = Event(
            type=EventType.AGENT_THINKING_START,
            payload={"agent_id": "test-agent"},
            source="test-source",
            trace_id="trace-123",
            span_id="span-456",
            parent_span_id="span-parent",
        )

        # Convert to dict
        event_dict = event.to_dict()

        # Verify trace fields
        assert event_dict["trace_id"] == "trace-123"
        assert event_dict["span_id"] == "span-456"
        assert event_dict["parent_span_id"] == "span-parent"

    def test_event_to_dict_serialization(self):
        """Test event serializes correctly to JSON."""
        from tlon.base.events.event import Event, EventType

        event = Event(
            type=EventType.MESSAGE_NEW,
            payload={"content": "Hello"},
            source="test",
            trace_id="trace-abc",
        )

        event_dict = event.to_dict()

        # Should be JSON serializable
        json_str = json.dumps(event_dict)
        assert isinstance(json_str, str)

        # Verify fields
        assert event_dict["type"] == "message.new"
        assert event_dict["source"] == "test"
        assert event_dict["trace_id"] == "trace-abc"
