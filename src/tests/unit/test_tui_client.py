"""Unit tests for TUI Client.

Tests cover:
- TUIClient command parsing
- /trace command handling
- Event display formatting
- Mode switching
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tlon.app.clients.tui.client import (
    AgentInfo,
    MentionCompleter,
    TUIClient,
    run_tui_client,
)


class TestAgentInfo:
    """Tests for AgentInfo class."""

    def test_init_default(self):
        """Test AgentInfo initialization with defaults."""
        agent = AgentInfo({})
        assert agent.id == "unknown"
        assert agent.name == "Unknown"
        assert agent.role == ""
        assert agent.state == "unknown"
        assert agent.emoji == "🤖"
        assert agent.color == "cyan"

    def test_init_with_data(self):
        """Test AgentInfo initialization with data."""
        data = {
            "id": "dev-001",
            "name": "林知遥",
            "role": "开发工程师",
            "state": "active",
        }
        agent = AgentInfo(data)
        assert agent.id == "dev-001"
        assert agent.name == "林知遥"
        assert agent.role == "开发工程师"
        assert agent.state == "active"
        assert agent.emoji == "💻"
        assert agent.color == "cyan"

    def test_status_icon(self):
        """Test status icon property."""
        active_agent = AgentInfo({"state": "active"})
        assert active_agent.status_icon == "🟢"

        init_agent = AgentInfo({"state": "initializing"})
        assert init_agent.status_icon == "🟡"

        thinking_agent = AgentInfo({"state": "thinking"})
        assert thinking_agent.status_icon == "🔵"

        unknown_agent = AgentInfo({"state": "unknown"})
        assert unknown_agent.status_icon == "⚪"


class TestMentionCompleter:
    """Tests for MentionCompleter class."""

    @pytest.fixture
    def mock_document(self):
        """Create a mock document."""
        doc = MagicMock()
        doc.get_word_before_cursor = MagicMock(return_value="@林")
        doc.cursor_position = 2
        doc.text = "@林"
        return doc

    @pytest.fixture
    def client_with_agents(self):
        """Create a TUIClient with some agents."""
        client = TUIClient()
        client.agents = {
            "dev-001": AgentInfo({"id": "dev-001", "name": "林知遥"}),
            "pm-001": AgentInfo({"id": "pm-001", "name": "张明"}),
        }
        return client

    def test_get_completions_by_name(self, client_with_agents, mock_document):
        """Test getting completions by agent name."""
        completer = MentionCompleter(client_with_agents)

        completions = list(completer.get_completions(mock_document, None))

        assert len(completions) == 1
        assert "@林知遥" in completions[0].text

    def test_get_completions_by_id(self, client_with_agents):
        """Test getting completions by agent ID."""
        completer = MentionCompleter(client_with_agents)

        doc = MagicMock()
        doc.get_word_before_cursor = MagicMock(return_value="@dev")
        doc.cursor_position = 4
        doc.text = "@dev"

        completions = list(completer.get_completions(doc, None))

        assert len(completions) >= 1
        assert any("@dev-001" in c.text for c in completions)

    def test_get_completions_no_prefix(self, client_with_agents):
        """Test getting completions without @ prefix."""
        completer = MentionCompleter(client_with_agents)

        doc = MagicMock()
        doc.get_word_before_cursor = MagicMock(return_value="hello")

        completions = list(completer.get_completions(doc, None))

        assert len(completions) == 0


class TestTUIClient:
    """Tests for TUIClient class."""

    @pytest.fixture
    def client(self):
        """Create a TUIClient instance."""
        return TUIClient(host="127.0.0.1", ws_port=8888, rest_port=8080)

    def test_init(self, client):
        """Test TUIClient initialization."""
        assert client.host == "127.0.0.1"
        assert client.ws_port == 8888
        assert client.rest_port == 8080
        assert client.ws_url == "ws://127.0.0.1:8888/ws"
        assert client.rest_url == "http://127.0.0.1:8080"
        assert client.current_mode == "chat"
        assert not client.connected

    @pytest.mark.asyncio
    async def test_connect(self, client):
        """Test connection."""
        with (
            patch("tlon.app.clients.sdk.TlonClient.connect", AsyncMock(return_value=True)),
            patch("tlon.app.clients.sdk.TlonClient.authenticate", AsyncMock(return_value=True)),
        ):
            result = await client.connect()

        assert result is True
        assert client._sdk is not None

    @pytest.mark.asyncio
    async def test_connect_failure(self, client):
        """Test connection failure."""
        with patch("tlon.app.clients.sdk.TlonClient.connect", AsyncMock(return_value=False)):
            result = await client.connect()

        assert result is False

    def test_extract_sender_info(self, client):
        """Test sender info extraction."""
        # With sender dict
        msg = {"sender": {"type": "agent", "name": "TestAgent"}}
        sender_type, sender_name = client._extract_sender_info(msg)
        assert sender_type == "agent"
        assert sender_name == "TestAgent"

        # With direct fields
        msg = {"sender_type": "human", "sender_name": "User"}
        sender_type, sender_name = client._extract_sender_info(msg)
        assert sender_type == "human"
        assert sender_name == "User"

        # Default values
        msg = {}
        sender_type, sender_name = client._extract_sender_info(msg)
        assert sender_type == "agent"
        assert sender_name == "Unknown"

    def test_format_timestamp(self, client):
        """Test timestamp formatting."""
        # Unix timestamp
        ts = 1700000000.0
        result = client._format_timestamp(ts)
        assert len(result) == 8  # HH:MM:SS
        assert ":" in result

        # String timestamp
        result = client._format_timestamp("1700000000.0")
        assert len(result) == 8

        # Invalid timestamp (should return current time)
        result = client._format_timestamp("invalid")
        assert len(result) == 8

    def test_format_event(self, client):
        """Test event formatting."""
        event = {
            "event_type": "agent.thinking",
            "payload": {"agent_id": "dev-001"},
            "timestamp": 1700000000.0,
            "trace_id": "trace-abc-123",
        }

        result = client._format_event(event)

        assert "agent.thinking" in result
        assert "dev-001" in result
        assert "[trace-ab]" in result  # Shortened trace_id
        assert "🧠" in result  # Icon

    def test_format_event_without_trace(self, client):
        """Test event formatting without trace_id."""
        event = {
            "event_type": "message.new",
            "payload": {"content": "Hello world"},
            "timestamp": 1700000000.0,
        }

        result = client._format_event(event)

        assert "message.new" in result
        assert "Hello" in result
        assert "📨" in result  # Icon

    @pytest.mark.asyncio
    async def test_handle_command_help(self, client, capsys):
        """Test /help command."""
        result = await client._handle_command("/help", [])

        assert result is True  # Continue running
        # Help text should be printed

    @pytest.mark.asyncio
    async def test_handle_command_quit(self, client):
        """Test /quit command."""
        result = await client._handle_command("/quit", [])

        assert result is False  # Signal to exit

    @pytest.mark.asyncio
    async def test_handle_command_mode(self, client):
        """Test /mode command."""
        # Set mode to event
        result = await client._handle_command("/mode", ["event"])

        assert result is True
        assert client.current_mode == "event"
        assert client._adapter.mode == "event"

    @pytest.mark.asyncio
    async def test_handle_command_mode_invalid(self, client):
        """Test /mode command with invalid mode."""
        original_mode = client.current_mode

        result = await client._handle_command("/mode", ["invalid"])

        assert result is True
        assert client.current_mode == original_mode  # Mode unchanged

    @pytest.mark.asyncio
    async def test_handle_command_trace(self, client):
        """Test /trace command."""
        client._sdk = AsyncMock()
        client._sdk.set_trace_filter = AsyncMock(return_value=True)
        client.current_mode = "event"  # Set mode to event for trace command

        result = await client._handle_command("/trace", ["trace-abc-123"])

        assert result is True
        assert client._current_trace_id == "trace-abc-123"
        assert client._adapter._trace_filter == "trace-abc-123"
        client._sdk.set_trace_filter.assert_called_once_with("trace-abc-123")

    @pytest.mark.asyncio
    async def test_handle_command_trace_clear(self, client):
        """Test /trace clear command."""
        client._sdk = AsyncMock()
        client._sdk.set_trace_filter = AsyncMock(return_value=True)
        client.current_mode = "event"  # Set mode to event for trace command

        # First set a trace
        await client._handle_command("/trace", ["trace-abc-123"])
        # Then clear it
        result = await client._handle_command("/trace", ["clear"])

        assert result is True
        assert client._current_trace_id is None
        assert client._adapter._trace_filter is None
        client._sdk.set_trace_filter.assert_called_with(None)

    @pytest.mark.asyncio
    async def test_handle_command_trace_no_args(self, client):
        """Test /trace command without arguments."""
        # No trace set
        result = await client._handle_command("/trace", [])

        assert result is True

        # Set a trace first
        client._current_trace_id = "trace-abc-123"
        result = await client._handle_command("/trace", [])

        assert result is True

    @pytest.mark.asyncio
    async def test_handle_command_filter(self, client):
        """Test /filter command."""
        result = await client._handle_command("/filter", ["agent"])

        assert result is True

    @pytest.mark.asyncio
    async def test_handle_command_filter_clear(self, client):
        """Test /filter clear command."""
        client.current_mode = "event"  # Set mode to event for filter command
        client._adapter.set_event_filters({"event_types": ["message.new"]})

        result = await client._handle_command("/filter", ["clear"])

        assert result is True
        assert client._adapter._event_filters is None

    @pytest.mark.asyncio
    async def test_handle_command_pause_resume(self, client):
        """Test /pause and /resume commands."""
        # Set mode to event for pause/resume to work
        client.current_mode = "event"

        # Pause
        result = await client._handle_command("/pause", [])
        assert result is True
        assert client.paused is True

        # Resume
        result = await client._handle_command("/resume", [])
        assert result is True
        assert client.paused is False

    @pytest.mark.asyncio
    async def test_handle_command_tail(self, client):
        """Test /tail command."""
        # Add some events
        client.events = [
            {"event_type": "message.new", "payload": {}},
            {"event_type": "agent.thinking", "payload": {}},
        ]

        result = await client._handle_command("/tail", ["10"])

        assert result is True

    @pytest.mark.asyncio
    async def test_handle_command_agents(self, client):
        """Test /agents command."""
        client.agents = {
            "dev-001": AgentInfo({"id": "dev-001", "name": "林知遥", "state": "active"}),
        }

        result = await client._handle_command("/agents", [])

        assert result is True

    @pytest.mark.asyncio
    async def test_handle_command_status(self, client):
        """Test /status command."""
        result = await client._handle_command("/status", [])

        assert result is True

    @pytest.mark.asyncio
    async def test_handle_command_clear(self, client):
        """Test /clear command."""
        with patch("tlon.app.clients.tui.client.console.clear") as mock_clear:
            result = await client._handle_command("/clear", [])

        assert result is True
        mock_clear.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_command_unknown(self, client):
        """Test unknown command."""
        result = await client._handle_command("/unknown", [])

        assert result is True  # Continue running

    def test_on_event_chat_mode(self, client):
        """Test event handling in chat mode."""
        client.current_mode = "chat"
        client.agents = {"dev-001": AgentInfo({"id": "dev-001", "name": "林知遥"})}

        # Message event should be processed
        message_event = {
            "type": "message.new",
            "payload": {
                "content": "Hello",
                "sender": {"type": "agent", "name": "林知遥"},
            },
        }

        with patch.object(client, "_print_chat_message") as mock_print:
            client._on_event(message_event)
            mock_print.assert_called_once()

    def test_on_event_agent_mode(self, client):
        """Test event handling in agent mode."""
        client.current_mode = "agent"

        # Agent event should be processed
        agent_event = {
            "type": "agent.thinking",
            "payload": {"agent_id": "dev-001"},
        }

        with patch.object(client, "_print_event") as mock_print:
            client._on_event(agent_event)
            mock_print.assert_called_once()

        # Non-agent event should not be processed
        message_event = {
            "type": "message.new",
            "payload": {"content": "Hello"},
        }

        mock_print.reset_mock()
        client._on_event(message_event)
        mock_print.assert_not_called()

    def test_on_event_stores_event(self, client):
        """Test that events are stored."""
        event = {
            "type": "message.new",
            "payload": {"content": "Hello"},
        }

        client._on_event(event)

        assert len(client.events) == 1
        assert client.events[0]["event_type"] == "message.new"

    def test_on_event_with_trace_id(self, client):
        """Test that trace_id is extracted from event."""
        event = {
            "type": "agent.thinking",
            "payload": {"agent_id": "dev-001"},
            "trace_id": "trace-abc-123",
            "span_id": "span-xyz-789",
        }

        client._on_event(event)

        stored_event = client.events[0]
        assert stored_event["trace_id"] == "trace-abc-123"
        assert stored_event["span_id"] == "span-xyz-789"

    def test_print_chat_message_human(self, client, capsys):
        """Test printing human message."""
        client._print_chat_message("human", "你", "Hello", 1700000000.0)

        captured = capsys.readouterr()
        assert "👤 你:" in captured.out
        assert "Hello" in captured.out

    def test_print_chat_message_system(self, client, capsys):
        """Test printing system message."""
        client._print_chat_message("system", "System", "Startup", 1700000000.0)

        captured = capsys.readouterr()
        assert "⚙️ System:" in captured.out
        assert "Startup" in captured.out

    def test_print_chat_message_agent(self, client, capsys):
        """Test printing agent message."""
        client.agents = {"dev-001": AgentInfo({"id": "dev-001", "name": "林知遥"})}
        client._print_chat_message("agent", "林知遥", "Hello", 1700000000.0)

        captured = capsys.readouterr()
        assert "💻 林知遥:" in captured.out
        assert "Hello" in captured.out

    def test_print_event_with_pause(self, client):
        """Test that events are queued when paused."""
        client.paused = True
        event = {"event_type": "message.new", "payload": {}}

        client._print_event(event)

        assert len(client._pending_events) == 1
        assert client._pending_events[0] == event

    def test_print_event_not_paused(self, client, capsys):
        """Test printing event when not paused."""
        client.paused = False
        event = {
            "event_type": "message.new",
            "payload": {"content": "Hello"},
            "timestamp": 1700000000.0,
        }

        client._print_event(event)

        captured = capsys.readouterr()
        assert "message.new" in captured.out

    @pytest.mark.asyncio
    async def test_load_agents(self, client):
        """Test loading agents."""
        client._sdk = AsyncMock()
        client._sdk.get_agents = AsyncMock(
            return_value=[
                {"id": "dev-001", "name": "林知遥", "role": "开发", "state": "active"},
            ]
        )

        await client.load_agents()

        assert "dev-001" in client.agents
        assert client.agents["dev-001"].name == "林知遥"

    @pytest.mark.asyncio
    async def test_load_history(self, client):
        """Test loading history."""
        client._sdk = AsyncMock()
        client._sdk.get_history = AsyncMock(
            return_value=[
                {"content": "Hello", "timestamp": 1700000000.0},
            ]
        )

        await client.load_history()

        assert len(client.messages) == 1

    @pytest.mark.asyncio
    async def test_send_message(self, client):
        """Test sending message."""
        client._sdk = AsyncMock()
        client._sdk.send_message = AsyncMock(return_value=True)

        result = await client.send_message("Hello", mentions=["dev-001"])

        assert result is True
        client._sdk.send_message.assert_called_once_with("Hello", ["dev-001"])

    @pytest.mark.asyncio
    async def test_show_agent_logs(self, client, capsys):
        """Test showing agent logs."""
        client._sdk = AsyncMock()
        client._sdk.get_agent_logs = AsyncMock(return_value="Log content here")

        await client._show_agent_logs("dev-001")

        captured = capsys.readouterr()
        assert "dev-001" in captured.out
        assert "Log content here" in captured.out


class TestRunTuiClient:
    """Tests for run_tui_client function."""

    @pytest.mark.asyncio
    async def test_run_tui_client(self):
        """Test run_tui_client function."""
        with patch("tlon.app.clients.tui.client.TUIClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.run = AsyncMock(return_value=0)
            mock_client_class.return_value = mock_client

            result = await run_tui_client("localhost", 8888, 8080)

            assert result == 0
            mock_client_class.assert_called_once_with(
                host="localhost", ws_port=8888, rest_port=8080
            )
            mock_client.run.assert_called_once()
