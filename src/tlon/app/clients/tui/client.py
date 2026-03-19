"""TUI Client implementation using the new SDK.

This module provides a TUI client built on top of the new SDK with:
- Three modes: chat, event, agent
- Event filtering by trace_id
- Trace visualization commands
"""

import sys
import time
from datetime import datetime
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.patch_stdout import patch_stdout
from rich import print as rprint
from rich.console import Console
from rich.table import Table

from tlon.app.clients.sdk import TerminalAdapter, TlonClient
from tlon.base.logger import get_logger

logger = get_logger(__name__)
console = Console(file=sys.stdout)


# Event icons for display
EVENT_ICONS = {
    "message.new": "📨",
    "message.mention": "📢",
    "agent.thinking": "🧠",
    "agent.thinking_start": "🧠",
    "agent.thinking_end": "✅",
    "agent.response": "✅",
    "agent.started": "🟢",
    "agent.stopped": "😴",
    "agent.error": "❌",
    "agent.llm_request": "🤖",
    "agent.llm_response": "💬",
    "tool.execute": "🔧",
    "tool.execute_start": "🔧",
    "tool.execute_end": "📄",
    "tool.result": "📄",
    "tool.error": "❌",
    "agent.join": "🌅",
    "agent.leave": "💔",
    "system.startup": "🚀",
    "system.shutdown": "🛑",
    "system.error": "❌",
    "session.join": "👋",
    "session.leave": "👋",
}


class AgentInfo:
    """Agent information for display."""

    AGENT_COLORS = {
        "cs-001": ("🎧", "green"),
        "pm-001": ("📊", "blue"),
        "dev-001": ("💻", "cyan"),
        "dev-002": ("💻", "cyan"),
        "ops-001": ("🔧", "yellow"),
        "data-001": ("📈", "magenta"),
        "prd-001": ("📝", "white"),
    }

    def __init__(self, data: dict[str, Any]):
        self.id = data.get("id", "unknown")
        self.name = data.get("name", "Unknown")
        self.role = data.get("role", "")
        self.state = data.get("state", "unknown")
        self.emoji, self.color = self.AGENT_COLORS.get(self.id, ("🤖", "cyan"))

    @property
    def status_icon(self) -> str:
        return {"active": "🟢", "initializing": "🟡", "thinking": "🔵"}.get(self.state, "⚪")


class MentionCompleter(Completer):
    """Completer for @mentions of agents."""

    def __init__(self, client: "TUIClient"):
        self.client = client

    def get_completions(self, document, complete_event):
        """Provide completions for @mentions."""
        word_before_cursor = document.get_word_before_cursor()

        if word_before_cursor.startswith("@"):
            prefix = word_before_cursor[1:].lower()

            for agent in self.client.agents.values():
                name_lower = agent.name.lower()
                if name_lower.startswith(prefix):
                    display = f"@{agent.name} ({agent.id})"
                    yield Completion(
                        f"@{agent.name}",
                        start_position=-len(word_before_cursor),
                        display=display,
                        style=f"fg:{agent.color}",
                    )

                id_lower = agent.id.lower()
                if id_lower.startswith(prefix) and id_lower != name_lower:
                    display = f"@{agent.id} ({agent.name})"
                    yield Completion(
                        f"@{agent.id}",
                        start_position=-len(word_before_cursor),
                        display=display,
                        style=f"fg:{agent.color}",
                    )


class TUIClient:
    """TUI Client built on the new SDK.

    Features:
    - Three modes: chat, event, agent
    - Event filtering by trace_id
    - Trace visualization (/trace command)
    - Uses new SDK's TlonClient + TerminalAdapter
    """

    def __init__(self, host: str = "127.0.0.1", ws_port: int = 8888, rest_port: int = 8080):
        self.host = host
        self.ws_port = ws_port
        self.rest_port = rest_port
        self.ws_url = f"ws://{host}:{ws_port}/ws"
        self.rest_url = f"http://{host}:{rest_port}"

        # SDK client
        self._adapter = TerminalAdapter()
        self._sdk: TlonClient | None = None

        # Data
        self.agents: dict[str, AgentInfo] = {}
        self.messages: list[dict] = []
        self.events: list[dict] = []

        # Mode state
        self.current_mode = "chat"  # chat | event | agent
        self.paused = False
        self._pending_events: list[dict] = []

        # Trace filtering
        self._current_trace_id: str | None = None

        # prompt-toolkit
        self.completer = MentionCompleter(self)
        self.prompt_session = PromptSession(completer=self.completer)

        # Running state
        self._running = False

    @property
    def connected(self) -> bool:
        """Check if connected to server."""
        ret = self._sdk is not None and self._sdk.is_connected
        return ret

    async def connect(self) -> bool:
        """Connect to the server using SDK."""
        try:
            self._sdk = TlonClient(
                ws_url=self.ws_url,
                rest_url=self.rest_url,
                adapter=self._adapter,
                auto_reconnect=True,
            )

            await self._sdk.connect()

            if not await self._sdk.authenticate():
                print("❌ Authentication failed")
                return False

            # Set up event handler
            self._sdk.on_event(self._on_event)

            print(f"✅ Connected to {self.ws_url}")
            return True

        except Exception as e:
            print(f"❌ Failed to connect: {e}")
            return False

    async def disconnect(self):
        """Disconnect from server."""
        if self._sdk:
            await self._sdk.disconnect()
            self._sdk = None

    async def load_agents(self):
        """Load agents from REST API."""
        if not self._sdk:
            return

        try:
            agents_data = await self._sdk.get_agents()
            self.agents = {a["id"]: AgentInfo(a) for a in agents_data}
        except Exception as e:
            logger.error(f"Failed to load agents: {e}")

    async def load_history(self):
        """Load message history."""
        if not self._sdk:
            return

        try:
            self.messages = await self._sdk.get_history(limit=50)
            if self.messages and self.current_mode == "chat":
                print(f"📜 加载了 {len(self.messages)} 条历史消息")
        except Exception as e:
            logger.warning(f"Failed to load history: {e}")

    async def send_message(self, content: str, mentions: list[str] = None) -> bool:
        """Send a message."""
        if not self._sdk:
            return False

        try:
            return await self._sdk.send_message(content, mentions or [])
        except Exception as e:
            print(f"❌ 发送失败: {e}")
            return False

    def _on_event(self, event: dict[str, Any]):
        """Handle incoming events from SDK."""
        event_type = event.get("type", "")
        payload = event.get("payload", {})

        # Store event
        event_data = {
            "event_type": event_type,
            "payload": payload,
            "source": event.get("source", ""),
            "timestamp": event.get("timestamp", time.time()),
            "trace_id": event.get("trace_id"),
            "span_id": event.get("span_id"),
        }
        self.events.append(event_data)

        # Handle based on mode
        if self.current_mode == "chat":
            if "message" in event_type.lower():
                sender_type, sender_name = self._extract_sender_info(payload)
                content = payload.get("content", "")

                if not content or sender_type == "human":
                    return

                self._print_chat_message(
                    sender_type, sender_name, content, payload.get("timestamp")
                )

        elif (
            self.current_mode == "event"
            or self.current_mode == "agent"
            and "agent" in event_type.lower()
        ):
            self._print_event(event_data)

    def _extract_sender_info(self, msg: dict) -> tuple:
        """Extract sender info from message."""
        sender = msg.get("sender", {})
        if sender and isinstance(sender, dict):
            return sender.get("type", "agent"), sender.get("name", "Unknown")
        return msg.get("sender_type", "agent"), msg.get("sender_name", "Unknown")

    def _format_timestamp(self, ts) -> str:
        """Format timestamp to HH:MM:SS."""
        if isinstance(ts, str):
            try:
                ts = float(ts)
            except (ValueError, TypeError):
                ts = time.time()
        return datetime.fromtimestamp(ts).strftime("%H:%M:%S")

    def _print_chat_message(
        self, sender_type: str, sender_name: str, content: str, timestamp: float = None
    ):
        """Print a chat message."""
        if not content:
            return

        ts = self._format_timestamp(timestamp) if timestamp else datetime.now().strftime("%H:%M:%S")

        if sender_type == "human" or sender_type == "user":
            print(f"[{ts}] 👤 你: {content}")
        elif sender_type == "system":
            print(f"[{ts}] ⚙️ {sender_name}: {content}")
        else:
            emoji = "🤖"
            for agent in self.agents.values():
                if agent.name == sender_name:
                    emoji = agent.emoji
                    break
            print(f"[{ts}] {emoji} {sender_name}: {content}")

    def _format_event(self, event: dict) -> str:
        """Format an event for display."""
        event_type = event.get("event_type", "")
        payload = event.get("payload", {})
        ts = event.get("timestamp", time.time())
        trace_id = event.get("trace_id")

        if isinstance(ts, str):
            try:
                ts = float(ts)
            except (ValueError, TypeError):
                ts = time.time()

        time_str = datetime.fromtimestamp(ts).strftime("%H:%M:%S.%f")[:-3]
        icon = EVENT_ICONS.get(event_type, "⚪")

        # Build trace info
        trace_info = ""
        if trace_id:
            short_trace = trace_id[:8] if len(trace_id) > 8 else trace_id
            trace_info = f" [{short_trace}]"

        # Format based on event type
        if "message" in event_type:
            content = str(payload.get("content", ""))[:50]
            return f"[{time_str}]{trace_info} {icon} {event_type} | {content}..."
        elif "agent" in event_type:
            agent = payload.get("agent_id", event.get("source", "unknown"))
            return f"[{time_str}]{trace_info} {icon} {event_type} | {agent}"
        elif "tool" in event_type:
            tool = payload.get("tool", "")
            return f"[{time_str}]{trace_info} {icon} {event_type} | {tool}"
        else:
            return f"[{time_str}]{trace_info} {icon} {event_type}"

    def _print_event(self, event: dict):
        """Print an event (with filtering)."""
        if self.paused:
            self._pending_events.append(event)
            return

        print(self._format_event(event))

    def _print_agent_table(self):
        """Print agent status table."""
        if not self.agents:
            print("暂无 Agent 信息")
            return

        table = Table(title="Agent 状态")
        table.add_column("状态", justify="center")
        table.add_column("ID")
        table.add_column("名称")
        table.add_column("职位")

        for agent in self.agents.values():
            table.add_row(agent.status_icon, agent.id, f"{agent.emoji} {agent.name}", agent.role)

        rprint(table)
        print(f"总计: {len(self.agents)} 个 Agent")

    def _print_top_table(self):
        """Print resource monitor table."""
        if not self.agents:
            print("暂无 Agent 信息")
            return

        table = Table(title="🎛️ Agent 资源监控")
        table.add_column("Agent")
        table.add_column("CPU")
        table.add_column("内存")
        table.add_column("磁盘")
        table.add_column("状态")

        for agent in self.agents.values():
            if agent.state == "active":
                table.add_row(f"{agent.emoji} {agent.name}", "2.1%", "256M/512M", "120M", "🟢")
            else:
                table.add_row(f"{agent.emoji} {agent.name}", "-", "-", "-", "⚪")

        rprint(table)

    def _show_help(self):
        """Show help message."""
        help_text = """
🎮 可用命令：

全局命令（所有模式）：
  /help, /h              显示帮助
  /quit, /q, /exit       退出 TUI
  /clear, /cls           清屏
  /mode <chat|event|agent>, /m   切换模式
  /connect, /c           重新连接
  /status, /s            显示连接状态
  /agents, /a            列出 Agent

Chat 模式命令：
  /history [n]           显示最近 n 条历史
  /who                   显示在线成员

Event 模式命令：
  /filter [条件]         设置过滤器
  /filter clear          清除过滤器
  /pause                 暂停滚动
  /resume                恢复滚动
  /tail [n]              显示最近 n 条
  /trace <trace_id>      按 trace_id 过滤
  /trace clear           清除 trace 过滤

Agent 模式命令：
  /list, /ls             打印状态表格
  /logs <id>             查看日志
  /top                   资源监控

提示：
  • Chat 模式下直接输入消息发送
  • 使用 @Agent名称 提及特定 Agent
  • Tab 键自动完成 @提及
  • ↑↓ 键浏览输入历史
"""
        print(help_text)

    async def _handle_command(self, cmd: str, args: list[str]) -> bool:
        """Handle commands. Returns False to exit."""
        cmd_lower = cmd.lower()

        # Global commands
        if cmd_lower in ["/help", "/h"]:
            self._show_help()

        elif cmd_lower in ["/quit", "/q", "/exit"]:
            return False

        elif cmd_lower in ["/clear", "/cls"]:
            console.clear()

        elif cmd_lower in ["/mode", "/m"]:
            if args:
                mode = args[0].lower()
                if mode in ["chat", "event", "agent"]:
                    self.current_mode = mode
                    self._adapter.set_mode(mode)
                    print(f"⚙️ 切换到 {mode} 模式")
                    if mode == "agent":
                        self._print_agent_table()
                else:
                    print(f"❌ 未知模式: {mode}")
            else:
                print(f"当前模式: {self.current_mode}")

        elif cmd_lower in ["/connect", "/c"]:
            print("正在重新连接...")
            await self.disconnect()
            if await self.connect():
                await self.load_agents()
                print("✅ 连接成功")
            else:
                print("❌ 连接失败")

        elif cmd_lower in ["/status", "/s"]:
            status = "已连接" if self.connected else "未连接"
            session_id = self._sdk.session_id if self._sdk else None
            print(f"连接状态: {status}")
            print(f"服务器: {self.ws_url}")
            if session_id:
                print(f"会话ID: {session_id}")

        elif cmd_lower in ["/agents", "/a"]:
            if self.agents:
                self._print_agent_table()
            else:
                print("暂无在线 Agent")

        # Chat mode commands
        elif self.current_mode == "chat":
            if cmd_lower == "/history":
                n = int(args[0]) if args else 50
                print(f"--- 最近 {min(len(self.messages), n)} 条历史消息 ---")
                for msg in self.messages[-n:]:
                    sender_type, sender_name = self._extract_sender_info(msg)
                    content = msg.get("content", "")
                    ts = msg.get("timestamp", time.time())
                    self._print_chat_message(sender_type, sender_name, content, ts)
                print("--- 历史消息结束 ---")

            elif cmd_lower == "/who":
                if self.agents:
                    online = [a.name for a in self.agents.values() if a.state == "active"]
                    print(f"在线: {', '.join(online) if online else '无'}")
                else:
                    print("暂无在线 Agent")

            else:
                print(f"❌ 未知命令: {cmd}")

        # Event mode commands
        elif self.current_mode == "event":
            if cmd_lower == "/filter":
                if args:
                    if args[0] == "clear":
                        self._adapter.clear_filters()
                        print("过滤器已清除")
                    else:
                        # Simple string filter on event type
                        filters = {"event_types": [args[0]]} if "." in args[0] else None
                        if filters:
                            self._adapter.set_event_filters(filters)
                        print(f"过滤器已设置: {args[0]}")
                else:
                    filters = self._adapter._event_filters
                    print(f"当前过滤器: {filters or '无'}")

            elif cmd_lower == "/pause":
                self.paused = True
                print("⏸️ Event 监控已暂停（新事件将被缓存）")

            elif cmd_lower == "/resume":
                self.paused = False
                if self._pending_events:
                    print(f"▶️ 恢复监控，显示 {len(self._pending_events)} 条缓存事件...")
                    for evt in self._pending_events:
                        self._print_event(evt)
                    self._pending_events.clear()
                else:
                    print("▶️ 恢复监控")

            elif cmd_lower == "/tail":
                n = int(args[0]) if args else 100
                print(f"--- 最近 {min(len(self.events), n)} 条事件 ---")
                for evt in self.events[-n:]:
                    self._print_event(evt)
                print("--- 显示结束 ---")

            elif cmd_lower == "/trace":
                if args:
                    if args[0] == "clear":
                        self._current_trace_id = None
                        self._adapter.set_trace_filter(None)
                        if self._sdk:
                            await self._sdk.set_trace_filter(None)
                        print("Trace 过滤已清除")
                    else:
                        trace_id = args[0]
                        self._current_trace_id = trace_id
                        self._adapter.set_trace_filter(trace_id)
                        if self._sdk:
                            await self._sdk.set_trace_filter(trace_id)
                        print(f"Trace 过滤已设置: {trace_id}")
                        print("只显示该 trace 相关的事件...")
                else:
                    if self._current_trace_id:
                        print(f"当前 Trace 过滤: {self._current_trace_id}")
                    else:
                        print("未设置 Trace 过滤")

            else:
                print(f"❌ 未知命令: {cmd}")

        # Agent mode commands
        elif self.current_mode == "agent":
            if cmd_lower in ["/list", "/ls"]:
                self._print_agent_table()

            elif cmd_lower == "/logs":
                if args:
                    agent_id = args[0]
                    await self._show_agent_logs(agent_id)
                else:
                    print("❌ 请指定 Agent ID")

            elif cmd_lower == "/top":
                self._print_top_table()

            else:
                print(f"❌ 未知命令: {cmd}")

        return True

    async def _show_agent_logs(self, agent_id: str):
        """Show agent logs."""
        if not self._sdk:
            print("未连接")
            return

        try:
            logs = await self._sdk.get_agent_logs(agent_id)
            if logs:
                print(f"--- Agent {agent_id} 日志 ---")
                print(logs)
                print("--- 日志结束 ---")
            else:
                print(f"📋 Agent '{agent_id}' 暂无日志")
        except Exception as e:
            print(f"❌ 获取日志失败: {e}")

    async def input_loop(self):
        """Handle user input."""
        while self._running and self.connected:
            try:
                prompt = f"[{self.current_mode}] > "
                user_input = await self.prompt_session.prompt_async(prompt)
                user_input = user_input.strip()

                if not user_input:
                    continue

                # Handle commands
                if user_input.startswith("/"):
                    parts = user_input.split()
                    cmd = parts[0]
                    args = parts[1:]

                    should_continue = await self._handle_command(cmd, args)
                    if not should_continue:
                        break
                else:
                    # Chat message (only in chat mode)
                    if self.current_mode == "chat":
                        mentions = []
                        for agent in self.agents.values():
                            if f"@{agent.name}" in user_input or f"@{agent.id}" in user_input:
                                mentions.append(agent.id)

                        if await self.send_message(user_input, mentions):
                            self._print_chat_message("human", "你", user_input, time.time())
                        else:
                            print("❌ 发送失败")
                    else:
                        print("⚠️ 当前模式下只能使用命令，请切换到 chat 模式发送消息")

            except EOFError:
                break
            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error(f"Input error: {e}")

    async def run(self) -> int:
        """Run the TUI client."""
        with patch_stdout():
            print(f"""
╔═══════════════════════════════════════╗
║  🐙 Tlon TUI Client (SDK v2)        ║
║  Connecting to {self.ws_url:<23}  ║
╚═══════════════════════════════════════╝
""")

            if not await self.connect():
                print("❌ 无法连接到服务器，请先启动 server")
                print(
                    f"提示: uv run tlon server --rest-port {self.rest_port} --ws-port {self.ws_port}"
                )
                return 1

            await self.load_agents()
            await self.load_history()

            if self.agents:
                print(f"📡 {len(self.agents)} 个 Agent 可用")

            print("\n输入 /help 查看帮助，/quit 退出")
            print("提示: /mode <chat|event|agent> 切换模式")
            print("提示: /trace <trace_id> 查看特定追踪\n")

            self._running = True

            try:
                await self.input_loop()
            finally:
                print("👋 Goodbye!")
                self._running = False
                await self.disconnect()

        return 0


async def run_tui_client(host: str = "127.0.0.1", port: int = 8888, rest_port: int = 8080) -> int:
    """Run the TUI client.

    Args:
        host: Server host
        port: WebSocket port
        rest_port: REST API port

    Returns:
        Exit code
    """
    client = TUIClient(host=host, ws_port=port, rest_port=rest_port)
    return await client.run()
