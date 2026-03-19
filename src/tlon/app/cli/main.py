"""Tlon CLI - Command line interface.

Provides commands (Borgesian metaphor):
- tlon dream    : Maintain the dream (start server) - from "The Circular Ruins"
- tlon mirror   : Create in the mirror (configure agents) - from "The Mirror and the Mask"
- tlon intrude  : Intrude into reality (connect interface) - Tlon invading reality

Usage:
    $ tlon dream            # Start server on default ports
    $ tlon dream --lucid    # Debug mode (lucid dream)
    $ tlon intrude          # Connect TUI client (default)
    $ tlon intrude tui      # Explicit TUI
    $ tlon mirror           # Run configuration wizard
"""

import argparse
import asyncio
import signal
import sys

from tlon.base.logger import configure_logging, get_logger
from tlon.base.settings import settings

logger = get_logger(__name__)


def create_parser() -> argparse.ArgumentParser:
    """Create the argument parser.

    Returns:
        Configured ArgumentParser
    """
    parser = argparse.ArgumentParser(
        prog="tlon",
        description="🐙 Tlon - A universe constructed of ideas, invading reality",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Literary Commands:
  %(prog)s dream            Maintain the dream (start server)
  %(prog)s dream --lucid    Lucid dream mode (debug)
  %(prog)s mirror           Create in the mirror (configure agents)
  %(prog)s mirror --reflect View existing agent mirrors
  %(prog)s intrude [tui]  Intrude into reality (connect UI)

Legacy Aliases (for backward compatibility):
  %(prog)s server           Same as 'dream'
  %(prog)s tui              Same as 'intrude'
  %(prog)s wizard           Same as 'mirror'
        """,
    )

    # Global options (apply to all commands including embedded mode)
    parser.add_argument("--version", action="version", version="%(prog)s 0.1.0")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode (verbose logging)")

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # dream command - maintain the dream (start server)
    dream_parser = subparsers.add_parser(
        "dream",
        aliases=["server"],  # Backward compatibility
        help="Maintain the dream (start server daemon)",
        description="""Start the Tlon server with REST and WebSocket APIs.

From "The Circular Ruins": The dreamer creates reality through dreams.
The server is the dream that sustains the Tlon universe.""",
    )
    dream_parser.add_argument(
        "--host", default="127.0.0.1", help="Host to bind to (default: 127.0.0.1)"
    )
    dream_parser.add_argument(
        "--rest-port", type=int, default=8080, help="Port for REST API (default: 8080)"
    )
    dream_parser.add_argument(
        "--ws-port", type=int, default=8888, help="Port for WebSocket (default: 8888)"
    )
    dream_parser.add_argument(
        "--lucid",
        "-l",
        dest="debug",
        action="store_true",
        help="Lucid dream mode - enable debug logging",
    )
    dream_parser.add_argument(
        "--debug", action="store_true", help=argparse.SUPPRESS
    )  # Hidden alias

    # intrude command - intrude into reality (connect UI)
    intrude_parser = subparsers.add_parser(
        "intrude",
        aliases=["tui"],  # Backward compatibility
        help="Intrude into reality (connect interface)",
        description="""Connect to the Tlon server through various interfaces.

Tlon is invading reality. You choose the point of intrusion.

From the story: The imagined universe of Tlon begins to manifest in the real world,
blurring the boundaries between fiction and reality.""",
    )
    intrude_parser.add_argument(
        "interface",
        nargs="?",
        choices=["tui"],
        default="tui",
        help="Interface to use for intrusion (default: tui)",
    )
    intrude_parser.add_argument(
        "--host", default="127.0.0.1", help="Server host (default: 127.0.0.1)"
    )
    intrude_parser.add_argument(
        "--port", "-p", type=int, default=8888, help="Server WebSocket port (default: 8888)"
    )
    intrude_parser.add_argument(
        "--rest-port", "-r", type=int, default=8080, help="Server REST API port (default: 8080)"
    )
    intrude_parser.add_argument(
        "--lucid",
        "-l",
        dest="debug",
        action="store_true",
        help="Lucid dream mode - enable debug logging",
    )
    intrude_parser.add_argument(
        "--debug", action="store_true", help=argparse.SUPPRESS
    )  # Hidden alias

    # mirror command - create in the mirror (configure agents)
    mirror_parser = subparsers.add_parser(
        "mirror",
        aliases=["wizard"],  # Backward compatibility
        help="Create in the mirror (configure agents)",
        description="""Interactive configuration wizard for creating and managing agents.

From "The Mirror and the Mask": The mirror reveals and creates.
Through the mirror, you shape the forms that will inhabit Tlon.

Use --reflect to view existing agent mirrors without entering the mirror.""",
    )
    mirror_parser.add_argument(
        "--reflect", "-r", action="store_true", help="View existing agent mirrors (list all agents)"
    )
    mirror_parser.add_argument(
        "--create",
        metavar="NAME",
        help="Create a new agent non-interactively (requires --position)",
    )
    mirror_parser.add_argument(
        "--position", metavar="POS", help="Position for the new agent (used with --create)"
    )
    mirror_parser.add_argument(
        "--template",
        metavar="TPL",
        default="default",
        help="Template for the new agent (default: default)",
    )
    mirror_parser.add_argument("--destroy", metavar="NAME", help="Destroy (remove) an agent")
    mirror_parser.add_argument(
        "--edit", metavar="NAME", help="Edit an existing agent's configuration"
    )

    return parser


def check_configuration() -> bool:
    """Check if the application is properly configured.

    Returns:
        True if configuration is valid
    """
    errors = []

    if not settings.llm_api_key:
        errors.append("OPENAI_API_KEY not set in .env file")

    if errors:
        print("\n" + "=" * 50)
        print("  Configuration Error")
        print("=" * 50)
        for error in errors:
            print(f"  ❌ {error}")
        print("\n  Please create a .env file with:")
        print("    OPENAI_API_KEY=your-moonshot-api-key-here")
        print("    OPENAI_BASE_URL=https://api.moonshot.cn/v1")
        print("    OPENAI_MODEL_NAME=kimi-k2.5")
        print("\n  You can copy from .env.example:")
        print("    cp .env.example .env")
        print("=" * 50 + "\n")
        return False

    return True


async def run_dream(args) -> int:
    """Run the dream command (start server).

    From "The Circular Ruins": The dreamer creates reality through dreams.

    Args:
        args: Parsed arguments

    Returns:
        Exit code
    """
    if not check_configuration():
        return 1

    from ..server.serverapp import run_server

    is_lucid = getattr(args, "debug", False)
    log_level = "DEBUG" if is_lucid else settings.log_level

    # Literary output
    print(
        """
[梦境开始] 特隆正在入侵现实...
           Server: ws://{}:{}
           代表大会: 等待连接
           Agents: 休眠中
{}""".format(args.host, args.ws_port, "           模式: 清醒梦 (lucid dream)\n" if is_lucid else "")
    )

    return await run_server(
        host=args.host,
        rest_port=args.rest_port,
        ws_port=args.ws_port,
        log_level=log_level,
        debug=is_lucid,
    )


async def run_intrude(args) -> int:
    """Run the intrude command (connect to interface).

    Tlon invades reality through the chosen interface.

    Args:
        args: Parsed arguments

    Returns:
        Exit code
    """
    is_lucid = getattr(args, "debug", False)
    log_level = "DEBUG" if is_lucid else settings.log_level
    configure_logging(log_level=log_level, debug=is_lucid)

    interface = getattr(args, "interface", "tui")

    if interface == "tui":
        print("\n正在突破现实边界...")
        if is_lucid:
            print("           模式: 清醒梦 (lucid dream)")
        print(f"连接至 ws://{args.host}:{args.port} ... ", end="", flush=True)

        # Use new SDK-based TUI client
        from tlon.app.clients.tui import run_tui_client

        # Register signal handlers for graceful shutdown (consistent with dream)
        client_task: asyncio.Task | None = None

        def _handle_signal(sig: signal.Signals) -> None:
            logger.info(f"Received signal {sig.name}, shutting down client...")
            if client_task and not client_task.done():
                client_task.cancel()

        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda s=sig: _handle_signal(s))

        try:
            client_task = asyncio.ensure_future(
                run_tui_client(
                    host=args.host, port=args.port, rest_port=getattr(args, "rest_port", 8080)
                )
            )
            result = await client_task
            return result
        except asyncio.CancelledError:
            print("\n\n入侵被中断...")
            return 130
        finally:
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.remove_signal_handler(sig)

    else:
        print(f"\n❌ 未知的入侵点: {interface}")
        return 1


async def run_mirror(args) -> int:
    """Run the mirror command (configure agents).

    From "The Mirror and the Mask": The mirror reveals and creates.

    Args:
        args: Parsed arguments

    Returns:
        Exit code
    """
    # Handle --reflect (list agents)
    if getattr(args, "reflect", False):
        return await _mirror_reflect()

    # Handle --destroy
    if getattr(args, "destroy", None):
        return await _mirror_destroy(args.destroy)

    # Handle --create
    if getattr(args, "create", None):
        return await _mirror_create(
            name=args.create,
            position=getattr(args, "position", None),
            template=getattr(args, "template", "default"),
        )

    # Handle --edit
    if getattr(args, "edit", None):
        return await _mirror_edit(args.edit)

    # Interactive mode
    print("""
你站在镜前，镜中的轮廓在等待...
""")

    from tlon.app.wizard.wizard import run_wizard

    return run_wizard()


async def _mirror_reflect() -> int:
    """List all existing agents (view mirrors)."""
    from tlon.base.config import ConfigLoader

    config_loader = ConfigLoader()
    agents = config_loader.load_all_agents()

    if not agents:
        print("\n🪞 镜中尚无倒影")
        print("   还没有创建任何 Agent")
        return 0

    print("\n🪞 镜中倒影:")
    print("-" * 50)

    for agent in agents.values():
        try:
            print(f"\n  📋 {agent.name} ({agent.id})")
            print(f"     职位: {agent.position_id}")
            if agent.personality:
                personality = agent.personality
                print(f"     性格: {personality[:40]}{'...' if len(personality) > 40 else ''}")
        except Exception as e:
            print(f"\n  ⚠️ {agent.id}: 读取失败 ({e})")

    print("\n-" * 50)
    print(f"总计: {len(agents)} 个 Agent")
    return 0


async def _mirror_destroy(name: str) -> int:
    """Destroy an agent."""
    from tlon.base.config import ConfigSaver

    config_saver = ConfigSaver()

    # Check if agent exists first
    from tlon.base.config import ConfigLoader

    config_loader = ConfigLoader()
    agent = config_loader.load_agent(name)

    if not agent:
        print(f"\n❌ 镜中没有名为 '{name}' 的倒影")
        return 1

    success, error = config_saver.delete_agent(name, delete_data=True)

    if success:
        print("\n🪞 倒影已消散")
        print(f"   Agent '{name}' 已被移除")
        return 0
    else:
        print(f"\n❌ 无法消散倒影: {error}")
        return 1


async def _mirror_create(name: str, position: str | None, template: str) -> int:
    """Create an agent non-interactively."""
    from tlon.base.config import (
        AgentConfig,
        ConfigLoader,
        ConfigSaver,
        DockerRuntimeConfig,
        LLMConfig,
    )
    from tlon.base.settings import settings
    from tlon.subsystem.memory import MemoryConfig

    if not position:
        print("\n❌ 必须指定 --position 来创建 Agent")
        print("   示例: tlon mirror --create my-agent --position developer")
        return 1

    # Validate position exists
    config_loader = ConfigLoader()
    position_config = config_loader.load_position(position)

    if not position_config:
        print(f"\n❌ 职位 '{position}' 不存在")
        # List available positions
        positions = config_loader.load_all_positions()
        if positions:
            available = list(positions.keys())
            print(f"   可用职位: {', '.join(available)}")
        return 1

    # Generate agent ID from name (pinyin/ascii)
    agent_id = _generate_agent_id(name)

    # Check if agent already exists
    existing = config_loader.load_agent(agent_id)
    if existing:
        print(f"\n❌ Agent '{agent_id}' 已存在")
        return 1

    # Get default skills from position
    default_skills = position_config.default_skills if position_config else []

    # Create agent config using ConfigSaver
    config_saver = ConfigSaver()

    agent_config = AgentConfig(
        id=agent_id,
        name=name,
        personality="专业、高效、善于协作",
        position_id=position,
        skills=default_skills,
        llm=LLMConfig(
            model_name=settings.llm_model_name or "kimi-k2.5",
            temperature=settings.llm_temperature or 0.7,
            base_url=settings.llm_base_url,
            auth_token=settings.llm_api_key,
        ),
        docker=DockerRuntimeConfig(
            image=settings.docker_image or "ubuntu:22.04",
            memory_limit=settings.docker_memory_limit or "512m",
        ),
        memory=MemoryConfig(agent_id=agent_id),
    )

    # Save agent config
    success, error = config_saver.save_agent(agent_config)

    if success:
        print("\n🪞 倒影已凝聚")
        print(f"   ID: {agent_id}")
        print(f"   名称: {name}")
        print(f"   职位: {position}")
        print(f"   配置: config/agents/{agent_id}.yaml")
        print("\n   [镜子] 轮廓凝聚，一个新的存在睁开眼睛。")
        return 0
    else:
        print(f"\n❌ 创建失败: {error}")
        return 1


def _generate_agent_id(name: str) -> str:
    """Generate agent ID from name (convert to pinyin/ascii lowercase with hyphens)."""
    import re

    # Simple conversion: lowercase, replace spaces with hyphens, remove special chars
    agent_id = name.lower()
    agent_id = re.sub(r"[^\w\s-]", "", agent_id)  # Remove special chars
    agent_id = re.sub(r"\s+", "-", agent_id)  # Replace spaces with hyphens
    agent_id = agent_id.strip("-")

    # If empty (e.g., all special chars), use a fallback
    if not agent_id:
        agent_id = "agent"

    return agent_id


async def _mirror_edit(name: str) -> int:
    """Edit an agent using default editor."""
    import os
    import subprocess

    from tlon.base.config import ConfigLoader

    # Find agent file
    config_loader = ConfigLoader()
    agent = config_loader.load_agent(name)

    if not agent:
        print(f"\n❌ 镜中没有名为 '{name}' 的倒影")
        return 1

    # Get agent file path
    agent_file = config_loader.agents_dir / f"{agent.id}.yaml"

    # Get editor from environment or use defaults
    editor = os.environ.get("EDITOR", "vi")
    if os.system(f"which {editor} > /dev/null 2>&1") != 0:
        # Try common editors
        for fallback in ["vim", "nano", "code", "notepad"]:
            if os.system(f"which {fallback} > /dev/null 2>&1") == 0:
                editor = fallback
                break

    print(f"\n🪞 编辑倒影: {agent.name}")
    print(f"   使用编辑器: {editor}")
    print(f"   配置文件: {agent_file}")

    try:
        subprocess.call([editor, str(agent_file)])
        print("\n✅ 编辑完成")
        return 0
    except Exception as e:
        print(f"\n❌ 无法启动编辑器: {e}")
        print(f"   请手动编辑: {agent_file}")
        return 1


async def main_async(args=None) -> int:
    """Main async entry point.

    Args:
        args: Pre-parsed arguments (optional)

    Returns:
        Exit code
    """
    if args is None:
        parser = create_parser()
        args = parser.parse_args()

    # Configure logging - use --debug flag if provided
    is_debug = getattr(args, "debug", False) or settings.debug
    configure_logging(
        log_level="DEBUG" if is_debug else settings.log_level,
        debug=is_debug,
    )

    # Route to appropriate command
    # Note: wizard is handled in main() due to its own event loop
    cmd = args.command

    # dream / server (start server)
    if cmd in ("dream", "server"):
        return await run_dream(args)

    # intrude / tui (connect interface)
    elif cmd in ("intrude", "tui"):
        return await run_intrude(args)

    # mirror / wizard (configure agents) - wizard handled in main()
    elif cmd == "mirror":
        return await run_mirror(args)

    else:
        # No command - show literary help
        print("""
🐙 Tlon - 一个由观念构建的、正在入侵现实的宇宙

Commands:
  dream       维持梦境（启动 Server）
  mirror      镜中造物（配置 Agent）
  intrude     入侵现实（连接界面）

Usage:
  tlon dream                    # 启动服务
  tlon dream --lucid            # Debug 模式
  tlon mirror                   # 配置向导
  tlon mirror --reflect         # 查看所有 Agents
  tlon intrude [tui]  # 连接界面

Run 'tlon --help' for more information.
""")
        return 0


def main() -> int:
    """Main entry point.

    Returns:
        Exit code
    """
    # Parse args first to determine if we need async
    parser = create_parser()
    args = parser.parse_args()

    # mirror/wizard uses questionary which has its own event loop
    # So we run it synchronously
    if args.command in ("mirror", "wizard"):
        try:
            # Import here to avoid circular imports
            from ..wizard.core import Wizard

            # Handle --reflect and other options that don't need interactive wizard
            if (
                getattr(args, "reflect", False)
                or getattr(args, "destroy", None)
                or getattr(args, "create", None)
                or getattr(args, "edit", None)
            ):
                # These are handled in async main_async, but since they don't
                # need the wizard UI, we can run them with a temporary event loop
                return asyncio.run(run_mirror(args))

            # Interactive wizard mode
            print("""
你站在镜前，镜中的轮廓在等待...
""")
            wizard = Wizard()
            wizard.run()
            return 0

        except ImportError:
            print("\n" + "=" * 50)
            print("  Wizard 依赖未安装")
            print("=" * 50)
            print("\n请安装以下依赖:")
            print("  pip install rich questionary")
            print("\n或者安装完整依赖:")
            print("  pip install -e '.[dev]'")
            print("=" * 50 + "\n")
            return 1
        except KeyboardInterrupt:
            print("\n\n镜中的倒影消散了...")
            return 130
        except Exception as e:
            logger.error(f"Wizard error: {e}", exc_info=True)
            print(f"\n❌ 错误: {e}")
            return 1

    # Other commands use asyncio
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\n\n入侵被中断...")
        return 130


if __name__ == "__main__":
    sys.exit(main())
