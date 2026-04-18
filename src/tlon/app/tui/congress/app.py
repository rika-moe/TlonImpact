"""CongressApp - Main TUI application for Congress.

Single-column chat interface with:
- Header bar with title and agent count
- Message scroll area
- Input area with agent selector and send button
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import Button, Header, Label

from tlon.app.clients.sdk import ConnectionState, TerminalAdapter, TlonClient
from tlon.base.logger import get_logger

from .components import (
    AgentSelector,
    ChatView,
    CommandPalette,
    InputBox,
    MentionPopup,
    MessageType,
)

logger = get_logger(__name__)


@dataclass
class AgentInfo:
    """Agent information for UI display."""

    id: str
    name: str
    state: str = "unknown"  # online, busy, idle, offline
    emoji: str = "🤖"
    color: str = "cyan"

    @property
    def status_icon(self) -> str:
        """Get status icon based on state."""
        icons = {
            "online": "🟢",
            "idle": "🟡",
            "busy": "🔴",
            "offline": "⚪",
            "unknown": "⚪",
        }
        return icons.get(self.state, "⚪")

    @property
    def display_name(self) -> str:
        """Get display name with emoji."""
        return f"{self.emoji} {self.name}"


class CongressApp(App[None]):
    """Congress TUI Application.

    A minimal chat interface for talking with agents.

    Layout:
        - Header: Title and online agent count
        - ChatView: Scrollable message history
        - InputArea: Agent selector + input box + send button

    Shortcuts:
        - Tab: Switch to next agent
        - Enter: Send message
        - Esc: Close popup or exit
        - Up/Down: Navigate input history
        - /: Open command palette
    """

    CSS_PATH = "styles/congress.tcss"

    BINDINGS = [
        Binding("tab", "next_agent", "Next Agent"),
        Binding("enter", "send_message", "Send"),
        Binding("escape", "escape", "Close/Exit"),
        Binding("up", "history_prev", "Prev History"),
        Binding("down", "history_next", "Next History"),
        Binding("slash", "command_palette", "Commands"),
        Binding("ctrl+c", "quit", "Quit"),
    ]

    # Reactive state
    agents: reactive[dict[str, AgentInfo]] = reactive({}, always_update=True)
    messages: reactive[list[dict[str, Any]]] = reactive([], always_update=True)
    current_agent_id: reactive[str | None] = reactive(None)
    input_history: reactive[list[str]] = reactive([])
    history_index: reactive[int] = reactive(-1)
    is_connected: reactive[bool] = reactive(False)

    def __init__(
        self,
        host: str = "127.0.0.1",
        ws_port: int = 8888,
        rest_port: int = 8080,
    ):
        """Initialize CongressApp.

        Args:
            host: Server host
            ws_port: WebSocket port
            rest_port: REST API port
        """
        super().__init__()
        self.host = host
        self.ws_port = ws_port
        self.rest_port = rest_port
        host_clean = (
            host.replace("ws://", "")
            .replace("wss://", "")
            .replace("http://", "")
            .replace("https://", "")
        )
        self.ws_url = f"ws://{host_clean}:{ws_port}/ws"
        self.rest_url = f"http://{host_clean}:{rest_port}"

        self._client: TlonClient | None = None
        self._input_buffer = ""

    def compose(self) -> ComposeResult:
        """Compose the UI layout."""
        yield Header(show_clock=True)

        with Vertical(id="main-container"):
            # Header with title and agent count
            with Horizontal(id="header-bar"):
                yield Label("💬 Congress", id="title")
                yield Label(id="agent-count")
                yield Label(id="connection-status")

            # Chat message area
            yield ChatView(id="chat-view")

            # Input area
            with Horizontal(id="input-area"):
                yield AgentSelector(id="agent-selector")
                yield InputBox(id="input-box")
                yield Button("发送", id="send-button", variant="primary")

        # Popups (initially hidden)
        yield MentionPopup(id="mention-popup")
        yield CommandPalette(id="command-palette")

    async def on_mount(self) -> None:
        """Called when app is mounted."""
        self.title = "Congress"
        self.sub_title = "与 Agent 对话"

        # Initialize components
        chat_view = self.query_one(ChatView)
        chat_view.focus()

        # Connect to server
        await self._connect()

    async def _connect(self) -> None:
        """Connect to Tlon server."""
        try:
            adapter = TerminalAdapter()
            self._client = TlonClient(
                ws_url=self.ws_url,
                adapter=adapter,
                rest_url=self.rest_url,
                auto_reconnect=True,
            )

            # Set up event handlers
            self._client.on_event(self._on_event)
            self._client.on_state_change(self._on_state_change)

            # Connect and authenticate
            connected = await self._client.connect()
            if connected:
                authenticated = await self._client.authenticate()
                if authenticated:
                    self.is_connected = True
                    await self._load_initial_data()
                    self._update_status("已连接")
                else:
                    self._update_status("认证失败")
            else:
                self._update_status("连接失败")

        except Exception as e:
            logger.error(f"Connection error: {e}")
            self._update_status(f"错误: {e}")

    async def _load_initial_data(self) -> None:
        """Load initial data from server."""
        if not self._client:
            return

        try:
            # Load agents
            agents_data = await self._client.get_agents()
            self._update_agents(agents_data)

            # Load history
            history = await self._client.get_history(limit=50)
            for msg in history:
                self._add_message_from_dict(msg)

        except Exception as e:
            logger.error(f"Failed to load initial data: {e}")

    def _update_agents(self, agents_data: list[dict]) -> None:
        """Update agents from server data."""
        new_agents = {}
        for data in agents_data:
            agent_id = data.get("id", "unknown")
            agent = AgentInfo(
                id=agent_id,
                name=data.get("name", "Unknown"),
                state=data.get("state", "unknown"),
                emoji=self._get_agent_emoji(data.get("role", "")),
            )
            new_agents[agent_id] = agent

        self.agents = new_agents

        # Update agent selector
        selector = self.query_one(AgentSelector)
        selector.update_agents(list(self.agents.values()))

        # Set default agent if none selected
        if self.current_agent_id is None and self.agents:
            first_agent = list(self.agents.values())[0]
            self.current_agent_id = first_agent.id
            selector.select_agent(first_agent.id)

        # Update header count
        self._update_agent_count()

    def _get_agent_emoji(self, role: str) -> str:
        """Get emoji based on agent role."""
        role_emojis = {
            "dev": "💻",
            "developer": "💻",
            "pm": "📊",
            "test": "🧪",
            "review": "👁️",
            "ops": "🔧",
            "default": "🤖",
        }
        return role_emojis.get(role.lower(), role_emojis["default"])

    def _update_agent_count(self) -> None:
        """Update agent count display."""
        count_label = self.query_one("#agent-count", Label)
        online_count = sum(1 for a in self.agents.values() if a.state == "online")
        count_label.update(f"在线: {online_count}/{len(self.agents)}")

    def _update_status(self, status: str) -> None:
        """Update connection status display."""
        status_label = self.query_one("#connection-status", Label)
        status_label.update(status)

    def _add_message_from_dict(self, msg: dict[str, Any]) -> None:
        """Add a message from dictionary."""
        sender_type = msg.get("sender_type", "agent")
        sender_name = msg.get("sender_name", "Unknown")
        content = msg.get("content", "")
        timestamp = msg.get("timestamp")

        # Parse timestamp
        time_str = ""
        if timestamp:
            try:
                if isinstance(timestamp, str):
                    dt = datetime.fromisoformat(timestamp)
                else:
                    dt = datetime.fromtimestamp(float(timestamp))
                time_str = dt.strftime("%H:%M")
            except (ValueError, TypeError):
                time_str = "--:--"

        # Determine message type
        msg_type = MessageType.AGENT
        if sender_type == "human":
            msg_type = MessageType.USER
        elif sender_type == "system":
            msg_type = MessageType.SYSTEM

        # Add to chat view
        chat_view = self.query_one(ChatView)
        chat_view.add_message(
            content=content,
            sender_name=sender_name,
            sender_type=msg_type,
            timestamp=time_str,
        )

    def _on_event(self, event: dict[str, Any]) -> None:
        """Handle incoming events from server."""
        event_type = event.get("type", "")
        payload = event.get("payload", {})

        if event_type in ("message.new", "message"):
            # New message
            self._add_message_from_dict(payload)
        elif event_type == "participants":
            # Participants update
            participants = payload.get("participants", [])
            self._update_agents_from_participants(participants)
        elif event_type == "agents":
            # Agent status update
            agents = payload.get("agents", [])
            self._update_agents(agents)

    def _update_agents_from_participants(self, participants: list[dict]) -> None:
        """Update agents from participants list."""
        for p in participants:
            if p.get("type") == "agent":
                agent_id = p.get("id")
                if agent_id and agent_id in self.agents:
                    # Update existing agent
                    pass  # State updates come via agents event

    def _on_state_change(self, state: Any) -> None:
        """Handle connection state changes."""
        # Normalize to enum for robust comparison
        if isinstance(state, ConnectionState):
            state_enum = state
        else:
            try:
                state_enum = ConnectionState(str(state).lower())
            except ValueError:
                return

        if state_enum == ConnectionState.AUTHENTICATED:
            self.is_connected = True
            self._update_status("已连接")
        elif state_enum == ConnectionState.DISCONNECTED:
            self.is_connected = False
            self._update_status("已断开")
        elif state_enum in (ConnectionState.CONNECTING, ConnectionState.RECONNECTING):
            self._update_status("连接中...")

    # ==================== Actions ====================

    async def action_next_agent(self) -> None:
        """Switch to next agent."""
        if not self.agents:
            return

        agent_list = list(self.agents.keys())
        if self.current_agent_id is None:
            next_index = 0
        else:
            try:
                current_index = agent_list.index(self.current_agent_id)
                next_index = (current_index + 1) % len(agent_list)
            except ValueError:
                next_index = 0

        self.current_agent_id = agent_list[next_index]
        selector = self.query_one(AgentSelector)
        selector.select_agent(self.current_agent_id)

    async def action_send_message(self) -> None:
        """Send current message."""
        input_box = self.query_one(InputBox)
        content = input_box.get_input().strip()

        if not content:
            return

        # Handle commands
        if content.startswith("/"):
            await self._handle_command(content)
            input_box.clear()
            return

        # Send message
        if self._client and self.is_connected:
            mentions = []
            if self.current_agent_id:
                mentions = [self.current_agent_id]

            try:
                await self._client.send_message(content, mentions=mentions)

                # Add to input history
                self.input_history.append(content)
                self.history_index = -1

                # Clear input
                input_box.clear()

            except Exception as e:
                logger.error(f"Failed to send message: {e}")
                self._show_error(f"发送失败: {e}")
        else:
            self._show_error("未连接到服务器")

    async def _handle_command(self, content: str) -> None:
        """Handle slash commands."""
        parts = content[1:].split()
        if not parts:
            return

        cmd = parts[0].lower()
        _ = parts[1:]

        if cmd == "clear":
            chat_view = self.query_one(ChatView)
            chat_view.clear_messages()
        elif cmd == "agents":
            await self._show_agents()
        elif cmd == "help":
            await self._show_help()
        elif cmd == "quit" or cmd == "exit":
            self.exit()
        else:
            self._show_error(f"未知命令: /{cmd}")

    async def _show_agents(self) -> None:
        """Show agent list in chat."""
        chat_view = self.query_one(ChatView)
        lines = ["**Agent 列表:**"]
        for agent in self.agents.values():
            lines.append(f"{agent.status_icon} {agent.display_name} ({agent.state})")

        chat_view.add_message(
            content="\n".join(lines),
            sender_name="System",
            sender_type=MessageType.SYSTEM,
            timestamp=datetime.now().strftime("%H:%M"),
        )

    async def _show_help(self) -> None:
        """Show help in chat."""
        help_text = """**可用命令:**

/clear - 清空对话
/agents - 查看 Agent 列表
/help - 显示帮助
/quit - 退出程序

**快捷键:**
Tab - 切换 Agent
Enter - 发送消息
Esc - 关闭弹窗/退出
↑/↓ - 浏览输入历史
/ - 快捷命令"""

        chat_view = self.query_one(ChatView)
        chat_view.add_message(
            content=help_text,
            sender_name="System",
            sender_type=MessageType.SYSTEM,
            timestamp=datetime.now().strftime("%H:%M"),
        )

    def action_escape(self) -> None:
        """Handle escape key."""
        # Close any open popups
        mention_popup = self.query_one(MentionPopup)
        command_palette = self.query_one(CommandPalette)

        if mention_popup.is_open:
            mention_popup.close()
        elif command_palette.is_open:
            command_palette.close()
        else:
            # Exit app
            self.exit()

    def action_history_prev(self) -> None:
        """Navigate to previous input history."""
        if not self.input_history:
            return

        if self.history_index < len(self.input_history) - 1:
            self.history_index += 1
            input_box = self.query_one(InputBox)
            input_box.set_input(self.input_history[-(self.history_index + 1)])

    def action_history_next(self) -> None:
        """Navigate to next input history."""
        input_box = self.query_one(InputBox)
        if self.history_index > 0:
            self.history_index -= 1
            input_box.set_input(self.input_history[-(self.history_index + 1)])
        elif self.history_index == 0:
            self.history_index = -1
            input_box.set_input("")

    def action_command_palette(self) -> None:
        """Open command palette."""
        palette = self.query_one(CommandPalette)
        palette.open()

    def _show_error(self, message: str) -> None:
        """Show error message."""
        chat_view = self.query_one(ChatView)
        chat_view.add_message(
            content=f"⚠️ {message}",
            sender_name="Error",
            sender_type=MessageType.SYSTEM,
            timestamp=datetime.now().strftime("%H:%M"),
        )

    async def on_input_box_changed(self, event: InputBox.Changed) -> None:
        """Handle input box changes."""
        text = event.value

        # Check for @mention trigger
        if "@" in text:
            # Get text after last @
            last_at = text.rfind("@")
            if last_at >= 0:
                query = text[last_at + 1 :]
                # Only trigger if no space in query
                if " " not in query:
                    popup = self.query_one(MentionPopup)
                    popup.open(query, self.agents.values())
                    return

        # Close mention popup if open
        popup = self.query_one(MentionPopup)
        if popup.is_open:
            popup.close()

    async def on_mention_popup_selected(self, event: MentionPopup.Selected) -> None:
        """Handle mention selection."""
        input_box = self.query_one(InputBox)
        current_text = input_box.get_input()

        # Replace the partial @mention with full name
        last_at = current_text.rfind("@")
        if last_at >= 0:
            new_text = current_text[: last_at + 1] + event.agent_name + " "
            input_box.set_input(new_text)

    async def on_agent_selector_changed(self, event: AgentSelector.Changed) -> None:
        """Handle agent selection change."""
        self.current_agent_id = event.agent_id

    async def on_command_palette_selected(self, event: CommandPalette.Selected) -> None:
        """Handle command palette selection."""
        input_box = self.query_one(InputBox)
        input_box.set_input(event.command)
        input_box.focus()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        if event.button.id == "send-button":
            await self.action_send_message()

    def on_unmount(self) -> None:
        """Cleanup when app unmounts."""
        if self._client:
            asyncio.create_task(self._client.disconnect())


async def run_congress(
    host: str = "127.0.0.1",
    ws_port: int = 8888,
    rest_port: int = 8080,
) -> None:
    """Run the Congress TUI application.

    Args:
        host: Server host
        ws_port: WebSocket port
        rest_port: REST API port
    """
    app = CongressApp(host=host, ws_port=ws_port, rest_port=rest_port)
    await app.run_async()


if __name__ == "__main__":
    asyncio.run(run_congress())
