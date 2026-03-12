"""Simple TUI Client - Minimal implementation with three modes.

This is the primary TUI implementation using stream-based console output.
Supports copy-paste and scrollback via terminal native capabilities.

Features:
- Three modes: chat, event, agent (switchable via /mode command)
- Stream-based output (no full-screen UI)
- Input line stays at bottom using prompt-toolkit's patch_stdout
- All commands start with /

Usage:
    $ tlon tui
    $ tlon tui -h 192.168.1.100 -w 8888
"""

import asyncio
import json
import time
from datetime import datetime
from typing import Dict, List, Optional, Any

import sys

import aiohttp
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.patch_stdout import patch_stdout
from rich import print as rprint
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from ..logger import get_logger

logger = get_logger(__name__)
# 创建 Console，使用标准输出
console = Console(file=sys.stdout)


# Event icons for display
EVENT_ICONS = {
    "message.new": "📨",
    "message.mention": "📢",
    "agent.thinking": "🧠",
    "agent.response": "✅",
    "agent.started": "🟢",
    "agent.stopped": "😴",
    "agent.error": "❌",
    "tool.execute": "🔧",
    "tool.result": "📄",
    "tool.error": "❌",
    "agent.join": "🌅",
    "agent.leave": "💔",
    "system.startup": "🚀",
    "system.shutdown": "🛑",
    "system.error": "❌",
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
        self.state = data.get("state", "standby")
        self.emoji, self.color = self.AGENT_COLORS.get(self.id, ("🤖", "cyan"))

    @property
    def status_icon(self) -> str:
        return {"active": "🟢", "waking": "🟡", "standby": "⚪"}.get(self.state, "⚪")


class MentionCompleter(Completer):
    """Completer for @mentions of agents."""

    def __init__(self, client: "SimpleTUIClient"):
        self.client = client

    def get_completions(self, document, complete_event):
        """Provide completions for @mentions."""
        text = document.text
        cursor_pos = document.cursor_position

        # Check if we're typing an @mention
        # Find the start of the current word
        word_before_cursor = document.get_word_before_cursor()

        # Check if current word starts with @ or if the text before cursor contains @
        if word_before_cursor.startswith('@'):
            prefix = word_before_cursor[1:].lower()

            # Get agent names and IDs for completion
            for agent in self.client.agents.values():
                # Complete by name
                name_lower = agent.name.lower()
                if name_lower.startswith(prefix):
                    display = f"@{agent.name} ({agent.id})"
                    yield Completion(
                        f"@{agent.name}",
                        start_position=-len(word_before_cursor),
                        display=display,
                        style=f"fg:{agent.color}",
                    )

                # Complete by ID
                id_lower = agent.id.lower()
                if id_lower.startswith(prefix) and id_lower != name_lower:
                    display = f"@{agent.id} ({agent.name})"
                    yield Completion(
                        f"@{agent.id}",
                        start_position=-len(word_before_cursor),
                        display=display,
                        style=f"fg:{agent.color}",
                    )


class SimpleTUIClient:
    """极简 TUI 客户端 - 流式控制台输出，支持三种模式"""

    def __init__(self, host: str = "127.0.0.1", ws_port: int = 8888, rest_port: int = 8080):
        self.host = host
        self.ws_port = ws_port
        self.rest_port = rest_port
        self.ws_url = f"ws://{host}:{ws_port}/ws"
        self.rest_url = f"http://{host}:{rest_port}"

        # Connection
        self.session: Optional[aiohttp.ClientSession] = None
        self.ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self.connected = False

        # Data
        self.agents: Dict[str, AgentInfo] = {}
        self.messages: List[dict] = []
        self.events: List[dict] = []

        # Mode state
        self.current_mode = "chat"  # chat | event | agent
        self.paused = False
        self.event_filters: Optional[str] = None
        self._pending_events: List[dict] = []

        # prompt-toolkit with mention completer
        self.completer = MentionCompleter(self)
        self.prompt_session = PromptSession(completer=self.completer)

    async def connect(self) -> bool:
        """Connect to WebSocket server."""
        try:
            self.session = aiohttp.ClientSession()
            self.ws = await self.session.ws_connect(self.ws_url)

            # Send authentication message
            auth_msg = {
                "type": "auth",
                "client_type": "tui",
                "metadata": {"version": "0.2.0"}
            }
            await self.ws.send_json(auth_msg)

            # Wait for auth response
            msg = await asyncio.wait_for(self.ws.receive(), timeout=10.0)
            if msg.type == aiohttp.WSMsgType.TEXT:
                data = json.loads(msg.data)
                if data.get("type") == "auth_success":
                    self.connected = True
                    print(f"✅ Connected to {self.ws_url}")
                    return True
                else:
                    print(f"❌ Authentication failed: {data.get('message', 'Unknown error')}")
            else:
                print("❌ Unexpected response from server")

            await self.ws.close()
            self.ws = None
            return False

        except asyncio.TimeoutError:
            print(f"❌ Connection timeout")
            return False
        except Exception as e:
            print(f"❌ Failed to connect: {e}")
            return False

    async def disconnect(self):
        """Disconnect from server."""
        if self.ws:
            await self.ws.close()
        if self.session:
            await self.session.close()
        self.connected = False

    async def load_agents(self):
        """Load agents from REST API."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.rest_url}/agents") as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        self.agents = {a["id"]: AgentInfo(a) for a in data.get("agents", [])}
        except Exception as e:
            logger.error(f"Failed to load agents: {e}")

    async def load_history(self):
        """Load message history."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.rest_url}/history?limit=50") as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        self.messages = data.get("messages", [])
                        if self.messages and self.current_mode == "chat":
                            print(f"📜 加载了 {len(self.messages)} 条历史消息")
        except Exception as e:
            logger.warning(f"Failed to load history: {e}")

    async def send_message(self, content: str, mentions: List[str] = None) -> bool:
        """Send a message."""
        if not self.connected or not self.ws:
            return False

        message = {
            "type": "message",
            "content": content,
            "mentions": mentions or [],
            "timestamp": time.time()
        }

        try:
            await self.ws.send_json(message)
            return True
        except Exception as e:
            print(f"❌ 发送失败: {e}")
            return False

    async def wake_agent(self, agent_id: str) -> bool:
        """Wake up an agent via REST API."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(f"{self.rest_url}/agents/{agent_id}/wake") as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        print(f"✅ {data.get('message', f'Agent {agent_id} woken')}")
                        # Refresh agent list
                        await self.load_agents()
                        return True
                    elif resp.status == 404:
                        print(f"❌ Agent '{agent_id}' 不存在")
                    else:
                        data = await resp.json()
                        print(f"❌ {data.get('message', f'Failed to wake {agent_id}')}")
                    return False
        except Exception as e:
            print(f"❌ 唤醒失败: {e}")
            return False

    async def sleep_agent(self, agent_id: str) -> bool:
        """Stop an agent via REST API."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(f"{self.rest_url}/agents/{agent_id}/stop") as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        print(f"✅ {data.get('message', f'Agent {agent_id} stopped')}")
                        # Refresh agent list
                        await self.load_agents()
                        return True
                    elif resp.status == 404:
                        print(f"❌ Agent '{agent_id}' 不存在")
                    else:
                        data = await resp.json()
                        print(f"❌ {data.get('message', f'Failed to stop {agent_id}')}")
                    return False
        except Exception as e:
            print(f"❌ 休眠失败: {e}")
            return False

    async def restart_agent(self, agent_id: str) -> bool:
        """Restart an agent (stop then wake)."""
        print(f"🔄 正在重启 {agent_id}...")
        if await self.sleep_agent(agent_id):
            # Give it a moment to stop
            await asyncio.sleep(1)
            return await self.wake_agent(agent_id)
        return False

    async def get_agent_logs(self, agent_id: str) -> Optional[str]:
        """Get agent logs (placeholder - not yet implemented in server)."""
        # TODO: Implement when server supports /agents/{id}/logs
        print(f"📋 Agent '{agent_id}' 的日志功能即将推出")
        print("   (服务器端 API 尚未实现)")
        return None

    def _extract_sender_info(self, msg: dict) -> tuple:
        """Extract sender info from message.
        Returns: (sender_type, sender_name)
        """
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

    def _print_chat_message(self, sender_type: str, sender_name: str, content: str, timestamp: float = None):
        """Print a chat message."""
        if not content:
            return

        ts = self._format_timestamp(timestamp) if timestamp else datetime.now().strftime("%H:%M:%S")

        if sender_type == "human" or sender_type == "user":
            print(f"[{ts}] 👤 你: {content}")
        elif sender_type == "system":
            print(f"[{ts}] ⚙️ {sender_name}: {content}")
        else:
            # Find agent emoji
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

        if isinstance(ts, str):
            try:
                ts = float(ts)
            except (ValueError, TypeError):
                ts = time.time()

        time_str = datetime.fromtimestamp(ts).strftime("%H:%M:%S.%f")[:-3]
        icon = EVENT_ICONS.get(event_type, "⚪")

        # Format based on event type
        if "message" in event_type:
            content = str(payload.get("content", ""))[:50]
            return f"[{time_str}] {icon} {event_type} | {content}..."
        elif "agent" in event_type:
            agent = payload.get("agent_id", event.get("source", "unknown"))
            return f"[{time_str}] {icon} {event_type} | {agent}"
        elif "tool" in event_type:
            tool = payload.get("tool", "")
            return f"[{time_str}] {icon} {event_type} | {tool}"
        else:
            return f"[{time_str}] {icon} {event_type}"

    def _print_event(self, event: dict):
        """Print an event (with filtering)."""
        if self.paused:
            self._pending_events.append(event)
            return

        # Apply filter
        if self.event_filters:
            event_type = event.get("event_type", "")
            if self.event_filters not in event_type:
                return

        print(self._format_event(event))

    def _should_show_event(self, event: dict) -> bool:
        """Check if event should be shown based on filters."""
        if not self.event_filters:
            return True
        event_type = event.get("event_type", "")
        return self.event_filters in event_type

    async def receive_loop(self):
        """Receive messages from server."""
        try:
            async for msg in self.ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        await self._handle_message(data)
                    except json.JSONDecodeError as e:
                        logger.warning(f"Invalid JSON received: {e}")
                    except Exception as e:
                        logger.error(f"Error handling message: {e}")
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    logger.error(f"WebSocket error: {self.ws.exception()}")
                    break
                elif msg.type == aiohttp.WSMsgType.CLOSED:
                    logger.info("WebSocket closed by server")
                    break
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Receive loop error: {e}")
        finally:
            self.connected = False
            print("\n⚠️ 与服务器的连接已断开")

    async def heartbeat_loop(self):
        """Send periodic heartbeat."""
        try:
            while self.connected:
                await asyncio.sleep(30)
                if self.connected and self.ws:
                    try:
                        await self.ws.send_json({"type": "ping"})
                    except Exception as e:
                        logger.debug(f"Heartbeat failed: {e}")
                        break
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug(f"Heartbeat error: {e}")

    async def _handle_message(self, data: dict):
        """Handle incoming message based on current mode."""
        msg_type = data.get("type")

        if msg_type == "event":
            event_type = data.get("event_type", "")
            payload = data.get("payload", {})

            # Store event
            self.events.append(data)

            # Handle based on mode
            if self.current_mode == "chat":
                # Chat mode: only show message events
                if "message" in event_type.lower():
                    sender_type, sender_name = self._extract_sender_info(payload)
                    content = payload.get("content", "")

                    if not content or sender_type == "human":
                        return

                    self._print_chat_message(sender_type, sender_name, content, payload.get("timestamp"))

            elif self.current_mode == "event":
                # Event mode: show all events (with filter)
                self._print_event(data)

            elif self.current_mode == "agent":
                # Agent mode: only show agent-related events
                if "agent" in event_type.lower():
                    self._print_event(data)

        elif msg_type == "message":
            # Direct message format
            payload = data.get("payload", {})
            sender_type, sender_name = self._extract_sender_info(payload)
            content = payload.get("content", "")

            if sender_type == "human":
                return

            if self.current_mode == "chat":
                self._print_chat_message(sender_type, sender_name, content, payload.get("timestamp"))

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
            table.add_row(
                agent.status_icon,
                agent.id,
                f"{agent.emoji} {agent.name}",
                agent.role
            )

        rprint(table)

        # Count stats
        active = sum(1 for a in self.agents.values() if a.state == "active")
        standby = sum(1 for a in self.agents.values() if a.state == "standby")
        print(f"在线: {active} | 待机: {standby} | 总计: {len(self.agents)}")

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
                table.add_row(
                    f"{agent.emoji} {agent.name}",
                    "2.1%",  # TODO: get real metrics
                    "256M/512M",
                    "120M",
                    "🟢"
                )
            else:
                table.add_row(
                    f"{agent.emoji} {agent.name}",
                    "-",
                    "-",
                    "-",
                    "⚪"
                )

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

Agent 模式命令：
  /list, /ls             打印状态表格
  /wake <id>             唤醒 Agent
  /sleep <id>            休眠 Agent
  /restart <id>          重启 Agent
  /logs <id>             查看日志
  /top                   资源监控

提示：
  • Chat 模式下直接输入消息发送
  • 使用 @Agent名称 提及特定 Agent
  • Tab 键自动完成 @提及
  • ↑↓ 键浏览输入历史
"""
        print(help_text)

    async def _handle_command(self, cmd: str, args: List[str]):
        """Handle commands."""
        cmd_lower = cmd.lower()

        # Global commands
        if cmd_lower in ['/help', '/h']:
            self._show_help()

        elif cmd_lower in ['/quit', '/q', '/exit']:
            return False  # Signal to exit

        elif cmd_lower in ['/clear', '/cls']:
            console.clear()

        elif cmd_lower in ['/mode', '/m']:
            if args:
                mode = args[0].lower()
                if mode in ['chat', 'event', 'agent']:
                    self.current_mode = mode
                    print(f"⚙️ 切换到 {mode} 模式")
                    if mode == "agent":
                        self._print_agent_table()
                else:
                    print(f"❌ 未知模式: {mode}")
            else:
                print(f"当前模式: {self.current_mode}")

        elif cmd_lower in ['/connect', '/c']:
            print("正在重新连接...")
            await self.disconnect()
            if await self.connect():
                await self.load_agents()
                print("✅ 连接成功")
            else:
                print("❌ 连接失败")

        elif cmd_lower in ['/status', '/s']:
            status = "已连接" if self.connected else "未连接"
            print(f"连接状态: {status}\n服务器: {self.ws_url}")

        elif cmd_lower in ['/agents', '/a']:
            if self.agents:
                self._print_agent_table()
            else:
                print("暂无在线 Agent")

        # Chat mode commands
        elif self.current_mode == "chat":
            if cmd_lower == '/history':
                n = int(args[0]) if args else 50
                print(f"--- 最近 {min(len(self.messages), n)} 条历史消息 ---")
                for msg in self.messages[-n:]:
                    sender_type, sender_name = self._extract_sender_info(msg)
                    content = msg.get("content", "")
                    ts = msg.get("timestamp", time.time())
                    self._print_chat_message(sender_type, sender_name, content, ts)
                print("--- 历史消息结束 ---")

            elif cmd_lower == '/who':
                if self.agents:
                    online = [a.name for a in self.agents.values() if a.state == "active"]
                    print(f"在线: {', '.join(online) if online else '无'}")
                else:
                    print("暂无在线 Agent")

            else:
                print(f"❌ 未知命令: {cmd}")

        # Event mode commands
        elif self.current_mode == "event":
            if cmd_lower == '/filter':
                if args:
                    if args[0] == 'clear':
                        self.event_filters = None
                        print("过滤器已清除")
                    else:
                        self.event_filters = args[0]
                        print(f"过滤器已设置: {args[0]}")
                else:
                    print(f"当前过滤器: {self.event_filters or '无'}")

            elif cmd_lower == '/pause':
                self.paused = True
                print("⏸️ Event 监控已暂停（新事件将被缓存）")

            elif cmd_lower == '/resume':
                self.paused = False
                if self._pending_events:
                    print(f"▶️ 恢复监控，显示 {len(self._pending_events)} 条缓存事件...")
                    for evt in self._pending_events:
                        self._print_event(evt)
                    self._pending_events.clear()
                else:
                    print("▶️ 恢复监控")

            elif cmd_lower == '/tail':
                n = int(args[0]) if args else 100
                print(f"--- 最近 {min(len(self.events), n)} 条事件 ---")
                for evt in self.events[-n:]:
                    self._print_event(evt)
                print("--- 显示结束 ---")

            else:
                print(f"❌ 未知命令: {cmd}")

        # Agent mode commands
        elif self.current_mode == "agent":
            if cmd_lower in ['/list', '/ls']:
                self._print_agent_table()

            elif cmd_lower == '/wake':
                if args:
                    agent_id = args[0]
                    await self.wake_agent(agent_id)
                else:
                    print("❌ 请指定 Agent ID")

            elif cmd_lower == '/sleep':
                if args:
                    agent_id = args[0]
                    await self.sleep_agent(agent_id)
                else:
                    print("❌ 请指定 Agent ID")

            elif cmd_lower == '/restart':
                if args:
                    agent_id = args[0]
                    await self.restart_agent(agent_id)
                else:
                    print("❌ 请指定 Agent ID")

            elif cmd_lower == '/logs':
                if args:
                    agent_id = args[0]
                    await self.get_agent_logs(agent_id)
                else:
                    print("❌ 请指定 Agent ID")

            elif cmd_lower == '/top':
                self._print_top_table()

            else:
                print(f"❌ 未知命令: {cmd}")

        return True  # Continue running

    async def input_loop(self):
        """Handle user input using prompt-toolkit."""
        while self.connected:
            try:
                # Build prompt with current mode
                prompt = f"[{self.current_mode}] > "

                user_input = await self.prompt_session.prompt_async(prompt)

                user_input = user_input.strip()
                if not user_input:
                    continue

                # Handle commands
                if user_input.startswith('/'):
                    parts = user_input.split()
                    cmd = parts[0]
                    args = parts[1:]

                    should_continue = await self._handle_command(cmd, args)
                    if not should_continue:
                        break
                else:
                    # Chat message (only in chat mode)
                    if self.current_mode == "chat":
                        # Parse mentions
                        mentions = []
                        for agent in self.agents.values():
                            if f"@{agent.name}" in user_input or f"@{agent.id}" in user_input:
                                mentions.append(agent.id)

                        # Send and echo locally
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
        # Use patch_stdout to keep input line at bottom for all output
        with patch_stdout():
            print(f"""
╔═══════════════════════════════════════╗
║  🐙 Tlon TUI Client (极简模式)      ║
║  Connecting to {self.ws_url:<23}  ║
╚═══════════════════════════════════════╝
""")

            # Connect
            if not await self.connect():
                print("❌ 无法连接到服务器，请先启动 server")
                print(f"提示: uv run tlon server --rest-port {self.rest_port} --ws-port {self.ws_port}")
                return 1

            # Load data
            await self.load_agents()
            await self.load_history()

            if self.agents:
                print(f"📡 {len(self.agents)} 个 Agent 可用")

            # Welcome message
            print("\n输入 /help 查看帮助，/quit 退出")
            print("提示: /mode <chat|event|agent> 切换模式\n")

            # Run receive, heartbeat and input concurrently
            receive_task = asyncio.create_task(self.receive_loop())
            heartbeat_task = asyncio.create_task(self.heartbeat_loop())

            try:
                await self.input_loop()
            finally:
                print("👋 Goodbye!")
                receive_task.cancel()
                heartbeat_task.cancel()
                try:
                    await receive_task
                except asyncio.CancelledError:
                    pass
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass
                await self.disconnect()

        return 0


async def run_tui_simple(host: str = "127.0.0.1", port: int = 8888, rest_port: int = 8080) -> int:
    """Run the simple TUI client.

    Args:
        host: Server host
        port: WebSocket port
        rest_port: REST API port

    Returns:
        Exit code
    """
    client = SimpleTUIClient(host=host, ws_port=port, rest_port=rest_port)
    return await client.run()
