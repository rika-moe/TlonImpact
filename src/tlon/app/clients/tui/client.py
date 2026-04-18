"""TUI Client implementation.

Provides a terminal user interface client for connecting to Tlon Server
and interacting with Agents.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document
from rich.console import Console

from tlon.app.clients.sdk import TerminalAdapter, TlonClient
from tlon.app.tui.common.styles.colors import get_agent_emoji
from tlon.base.logger import get_logger

logger = get_logger(__name__)
console = Console()


@dataclass
class AgentInfo:
    """Information about an Agent.

    Attributes:
        id: Unique agent identifier
        name: Display name
        role: Agent role description
        state: Current agent state
        emoji: Emoji representation
        color: Color for display
    """

    id: str = "unknown"
    name: str = "Unknown"
    role: str = ""
    state: str = "unknown"
    emoji: str = "🤖"
    color: str = "cyan"

    def __init__(self, data: dict[str, Any] | None = None):
        """Initialize from dictionary.

        Args:
            data: Dictionary containing agent data
        """
        data = data or {}
        self.id = data.get("id", "unknown")
        self.name = data.get("name", "Unknown")
        self.role = data.get("role", "")
        self.state = data.get("state", "unknown")
        self.emoji = data.get("emoji", "🤖")
        self.color = data.get("color", "cyan")

        # Set emoji based on role
        if self.emoji == "🤖" and self.role:
            self.emoji = get_agent_emoji(self.role)

    @property
    def display_name(self) -> str:
        """Get display name with emoji.

        Returns:
            Formatted display name
        """
        return f"{self.emoji} {self.name}"

    @property
    def status_icon(self) -> str:
        """Get status icon based on agent state.

        Returns:
            Status icon emoji
        """
        icons = {
            "active": "🟢",
            "idle": "🟢",
            "initializing": "🟡",
            "thinking": "🔵",
            "busy": "🟠",
            "offline": "⚪",
            "unknown": "⚪",
        }
        return icons.get(self.state, "⚪")


class MentionCompleter(Completer):
    """Completer for @mentions in input.

    Provides autocomplete suggestions for agent mentions.
    """

    def __init__(self, client: TUIClient):
        """Initialize with TUI client.

        Args:
            client: TUIClient instance
        """
        self._client = client

    def get_completions(self, document: Document, complete_event: Any) -> list[Completion]:
        """Get completions for the current word.

        Args:
            document: Current document
            complete_event: Completion event

        Returns:
            List of completions
        """
        word = document.get_word_before_cursor()

        if not word.startswith("@"):
            return []

        query = word[1:].lower()
        completions = []

        for agent in self._client.agents.values():
            # Match by name or ID
            if query in agent.name.lower() or query in agent.id.lower():
                # Use id for completion text if matched by id, otherwise use name
                text = f"@{agent.id}" if query in agent.id.lower() else f"@{agent.name}"
                display = f"@{agent.name} ({agent.id})"
                completions.append(
                    Completion(
                        text=text,
                        start_position=-len(word),
                        display=display,
                    )
                )

        return completions


class TUIClient:
    """Terminal User Interface Client for Tlon.

    Provides a command-line interface for interacting with the Tlon server,
    including chat, event monitoring, and agent management.

    Attributes:
        host: Server host
        ws_port: WebSocket port
        rest_port: REST API port
        ws_url: WebSocket URL
        rest_url: REST API URL
        current_mode: Current display mode (chat, event, agent)
        connected: Whether connected to server
        agents: Dictionary of known agents
        messages: List of chat messages
        events: List of events
        paused: Whether event display is paused
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        ws_port: int = 8888,
        rest_port: int = 8080,
    ):
        """Initialize TUI client.

        Args:
            host: Server host
            ws_port: WebSocket port
            rest_port: REST API port
        """
        self.host = host
        self.ws_port = ws_port
        self.rest_port = rest_port
        self.ws_url = f"ws://{host}:{ws_port}/ws"
        self.rest_url = f"http://{host}:{rest_port}"

        self.current_mode = "chat"
        self.connected = False
        self.paused = False

        # Data storage
        self.agents: dict[str, AgentInfo] = {}
        self.messages: list[dict] = []
        self.events: list[dict] = []
        self._pending_events: list[dict] = []

        # SDK client
        self._sdk: TlonClient | None = None
        self._adapter = TerminalAdapter()

        # State
        self._current_trace_id: str | None = None

    async def connect(self) -> bool:
        """Connect to the server.

        Returns:
            True if connected successfully
        """
        try:
            self._sdk = TlonClient(
                ws_url=self.ws_url,
                adapter=self._adapter,
                rest_url=self.rest_url,
                auto_reconnect=True,
            )

            # Set up event handlers
            self._sdk.on_event(self._on_event)

            # Connect and authenticate
            connected = await self._sdk.connect()
            if not connected:
                return False

            authenticated = await self._sdk.authenticate()
            if not authenticated:
                return False

            # Set initial mode
            await self._sdk.set_mode(self.current_mode)

            self.connected = True
            return True

        except Exception as e:
            logger.error(f"Connection failed: {e}")
            return False

    async def disconnect(self) -> None:
        """Disconnect from the server."""
        if self._sdk:
            await self._sdk.disconnect()
        self.connected = False

    def _on_event(self, event: dict[str, Any]) -> None:
        """Handle incoming events.

        Args:
            event: Event data
        """
        event_type = event.get("type", "")
        payload = event.get("payload", {})

        # Store event with metadata
        stored_event = {
            "event_type": event_type,
            "payload": payload,
            "timestamp": event.get("timestamp"),
            "trace_id": event.get("trace_id"),
            "span_id": event.get("span_id"),
        }
        self.events.append(stored_event)

        # Handle based on mode
        if self.current_mode == "chat":
            if event_type in ("message.new", "agent.response"):
                self._print_chat_message_from_event(event)
        elif self.current_mode == "agent":
            if event_type.startswith("agent."):
                self._print_event(stored_event)
        elif self.current_mode == "event":
            self._print_event(stored_event)

    def _print_chat_message_from_event(self, event: dict[str, Any]) -> None:
        """Print a chat message from an event.

        Args:
            event: Event data
        """
        payload = event.get("payload", {})
        sender = payload.get("sender", {})
        sender_type = sender.get("type", "agent")
        sender_name = sender.get("name", "Unknown")
        content = payload.get("content", "")
        timestamp = event.get("timestamp")

        self._print_chat_message(sender_type, sender_name, content, timestamp)

    def _print_chat_message(
        self,
        sender_type: str,
        sender_name: str,
        content: str,
        timestamp: float | None = None,
    ) -> None:
        """Print a chat message.

        Args:
            sender_type: Type of sender (human, agent, system)
            sender_name: Name of sender
            content: Message content
            timestamp: Optional timestamp
        """
        time_str = self._format_timestamp(timestamp) if timestamp else ""

        icons = {
            "human": "👤",
            "agent": "🤖",
            "system": "⚙️",
        }
        icon = icons.get(sender_type, "🤖")

        # Get agent emoji if available
        if sender_type == "agent":
            for agent in self.agents.values():
                if agent.name == sender_name:
                    icon = agent.emoji
                    break

        print(f"[{time_str}] {icon} {sender_name}: {content}")

    def _print_event(self, event: dict[str, Any]) -> None:
        """Print an event.

        Args:
            event: Event data
        """
        if self.paused:
            self._pending_events.append(event)
            return

        formatted = self._format_event(event)
        print(formatted)

    def _format_event(self, event: dict[str, Any]) -> str:
        """Format an event for display.

        Args:
            event: Event data

        Returns:
            Formatted event string
        """
        event_type = event.get("event_type", "unknown")
        payload = event.get("payload", {})
        timestamp = event.get("timestamp")
        trace_id = event.get("trace_id")

        time_str = self._format_timestamp(timestamp) if timestamp else ""
        trace_str = f" [{trace_id[:8]}]" if trace_id else ""

        # Event type icons
        icons = {
            "message.new": "📨",
            "agent.thinking": "🧠",
            "agent.response": "💬",
            "agent.started": "🚀",
            "agent.stopped": "🛑",
            "agent.error": "❌",
        }
        icon = icons.get(event_type, "📋")

        # Format payload
        if "content" in payload:
            content = (
                payload["content"][:50] + "..."
                if len(payload["content"]) > 50
                else payload["content"]
            )
        elif "agent_id" in payload:
            content = f"agent_id={payload['agent_id']}"
        else:
            content = str(payload)[:50]

        return f"[{time_str}]{trace_str} {icon} {event_type}: {content}"

    def _format_timestamp(self, timestamp: float | str | None) -> str:
        """Format a timestamp for display.

        Args:
            timestamp: Unix timestamp or ISO string

        Returns:
            Formatted time string (HH:MM:SS)
        """
        try:
            if isinstance(timestamp, str):
                timestamp = float(timestamp)
            if isinstance(timestamp, (int, float)):
                dt = datetime.fromtimestamp(timestamp)
                return dt.strftime("%H:%M:%S")
        except (ValueError, TypeError):
            pass

        # Return current time as fallback
        return datetime.now().strftime("%H:%M:%S")

    def _extract_sender_info(self, msg: dict[str, Any]) -> tuple[str, str]:
        """Extract sender info from message.

        Args:
            msg: Message data

        Returns:
            Tuple of (sender_type, sender_name)
        """
        sender = msg.get("sender", {})
        sender_type = sender.get("type", "agent")
        sender_name = sender.get("name", "Unknown")

        # Fallback to direct fields
        if sender_type == "agent" and "sender_type" in msg:
            sender_type = msg["sender_type"]
        if sender_name == "Unknown" and "sender_name" in msg:
            sender_name = msg["sender_name"]

        return sender_type, sender_name

    async def _handle_command(self, command: str, args: list[str]) -> bool:
        """Handle a command.

        Args:
            command: Command name (e.g., "/help")
            args: Command arguments

        Returns:
            False to exit, True to continue
        """
        cmd = command.lower()

        if cmd == "/quit" or cmd == "/exit":
            return False

        elif cmd == "/help":
            self._print_help()

        elif cmd == "/mode":
            if args:
                mode = args[0].lower()
                if mode in ("chat", "event", "agent"):
                    self.current_mode = mode
                    self._adapter.set_mode(mode)
                    if self._sdk:
                        await self._sdk.set_mode(mode)
                    print(f"Mode changed to: {mode}")
                else:
                    print(f"Invalid mode: {mode}. Use: chat, event, agent")
            else:
                print(f"Current mode: {self.current_mode}")

        elif cmd == "/trace":
            if args:
                if args[0].lower() == "clear":
                    self._current_trace_id = None
                    self._adapter.set_trace_filter(None)
                    if self._sdk:
                        await self._sdk.set_trace_filter(None)
                    print("Trace filter cleared")
                else:
                    trace_id = args[0]
                    self._current_trace_id = trace_id
                    self._adapter.set_trace_filter(trace_id)
                    if self._sdk:
                        await self._sdk.set_trace_filter(trace_id)
                    print(f"Trace filter set: {trace_id}")
            else:
                if self._current_trace_id:
                    print(f"Current trace: {self._current_trace_id}")
                else:
                    print("No trace filter set")

        elif cmd == "/filter":
            if args:
                if args[0].lower() == "clear":
                    self._adapter.clear_filters()
                    print("Filters cleared")
                else:
                    # Set event type filter
                    event_types = args
                    self._adapter.set_event_filters({"event_types": event_types})
                    print(f"Event filter set: {event_types}")
            else:
                print("Usage: /filter <event_type1> [event_type2...] or /filter clear")

        elif cmd == "/pause":
            self.paused = True
            print("Event display paused")

        elif cmd == "/resume":
            self.paused = False
            # Print pending events
            for event in self._pending_events:
                self._print_event(event)
            self._pending_events.clear()
            print("Event display resumed")

        elif cmd == "/tail":
            n = int(args[0]) if args else 10
            for event in self.events[-n:]:
                self._print_event(event)

        elif cmd == "/agents":
            print("Agents:")
            for agent in self.agents.values():
                print(f"  {agent.status_icon} {agent.display_name} ({agent.id})")

        elif cmd == "/status":
            print(f"Connected: {self.connected}")
            print(f"Mode: {self.current_mode}")
            print(f"Events: {len(self.events)}")
            print(f"Agents: {len(self.agents)}")

        elif cmd == "/clear":
            console.clear()

        else:
            print(f"Unknown command: {command}")

        return True

    def _print_help(self) -> None:
        """Print help text."""
        help_text = """
Available commands:
  /help              Show this help
  /quit, /exit       Exit the client
  /mode <mode>       Set mode (chat, event, agent)
  /trace <id>        Filter by trace ID
  /trace clear       Clear trace filter
  /filter <types>    Filter event types
  /filter clear      Clear event filters
  /pause             Pause event display
  /resume            Resume event display
  /tail [n]          Show last n events
  /agents            List agents
  /status            Show connection status
  /clear             Clear screen
"""
        print(help_text)

    async def load_agents(self) -> None:
        """Load agents from server."""
        if not self._sdk:
            return

        try:
            agents_data = await self._sdk.get_agents()
            for agent_data in agents_data:
                agent = AgentInfo(agent_data)
                self.agents[agent.id] = agent
        except Exception as e:
            logger.error(f"Failed to load agents: {e}")

    async def load_history(self) -> None:
        """Load message history from server."""
        if not self._sdk:
            return

        try:
            messages = await self._sdk.get_history(limit=50)
            self.messages.extend(messages)
        except Exception as e:
            logger.error(f"Failed to load history: {e}")

    async def send_message(self, content: str, mentions: list[str] | None = None) -> bool:
        """Send a message.

        Args:
            content: Message content
            mentions: Optional list of agent IDs to mention

        Returns:
            True if sent successfully
        """
        if not self._sdk:
            return False

        try:
            return await self._sdk.send_message(content, mentions)
        except Exception as e:
            logger.error(f"Failed to send message: {e}")
            return False

    async def _show_agent_logs(self, agent_id: str) -> None:
        """Show logs for an agent.

        Args:
            agent_id: Agent ID
        """
        if not self._sdk:
            return

        try:
            logs = await self._sdk.get_agent_logs(agent_id)
            if logs:
                print(f"Logs for {agent_id}:")
                print(logs)
            else:
                print(f"No logs available for {agent_id}")
        except Exception as e:
            logger.error(f"Failed to get logs: {e}")

    async def run(self) -> int:
        """Run the TUI client.

        Returns:
            Exit code
        """
        # Connect
        if not await self.connect():
            print("Failed to connect to server")
            return 1

        print(f"Connected to {self.ws_url}")
        print("Type /help for available commands")

        # Load initial data
        await self.load_agents()
        await self.load_history()

        # Main loop
        try:
            while True:
                # Get user input
                user_input = input("> ").strip()

                if not user_input:
                    continue

                # Check for commands
                if user_input.startswith("/"):
                    parts = user_input.split()
                    command = parts[0]
                    args = parts[1:]

                    should_continue = await self._handle_command(command, args)
                    if not should_continue:
                        break
                else:
                    # Send as message
                    await self.send_message(user_input)

        except KeyboardInterrupt:
            print("\nInterrupted")
        finally:
            await self.disconnect()

        return 0


async def run_tui_client(
    host: str = "localhost",
    ws_port: int = 8888,
    rest_port: int = 8080,
) -> int:
    """Run the TUI client.

    Args:
        host: Server host
        ws_port: WebSocket port
        rest_port: REST API port

    Returns:
        Exit code
    """
    client = TUIClient(host=host, ws_port=ws_port, rest_port=rest_port)
    return await client.run()
