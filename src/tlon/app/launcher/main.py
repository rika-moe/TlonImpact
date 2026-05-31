"""Tlon CLI - Command line interface.

Provides commands (Borgesian metaphor):
- tlon dream    : Maintain the dream (start server) - from "The Circular Ruins"
- tlon mirror   : Create in the mirror (configure agents) - from "The Mirror and the Mask"
- tlon chat     : Enter Congress (chat with agents)
- tlon ops      : Open Operator console (monitor and manage)

Usage:
    $ tlon dream            # Start server on default ports
    $ tlon dream --lucid    # Debug mode (lucid dream)
    $ tlon chat             # Connect chat TUI
    $ tlon ops              # Connect operator TUI
    $ tlon mirror           # Run configuration wizard
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import TYPE_CHECKING

from tlon.app.launcher.commands.chat import run_chat
from tlon.app.launcher.commands.dream import run_dream
from tlon.app.launcher.commands.mirror import run_mirror
from tlon.app.launcher.commands.ops import run_ops
from tlon.base.logger import configure_logging
from tlon.base.settings import get_settings

if TYPE_CHECKING:
    from argparse import Namespace


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
Commands:
  %(prog)s dream            Maintain the dream (start server)
  %(prog)s dream --lucid    Lucid dream mode (debug)
  %(prog)s chat             Enter Congress (chat TUI)
  %(prog)s ops              Open Operator console (ops TUI)
  %(prog)s mirror           Create in the mirror (configure agents)

Legacy Aliases (for backward compatibility):
  %(prog)s server           Same as 'dream'
  %(prog)s intrude          Same as 'ops'
  %(prog)s tui              Same as 'ops'
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

    # chat command - enter Congress (chat TUI)
    chat_parser = subparsers.add_parser(
        "chat",
        help="Enter Congress (chat with agents)",
        description="""Connect to the Tlon server through the Congress chat interface.

Talk with agents in a terminal-based chat room.""",
    )
    chat_parser.add_argument("--host", default="127.0.0.1", help="Server host (default: 127.0.0.1)")
    chat_parser.add_argument(
        "--port", "-p", type=int, default=8888, help="Server WebSocket port (default: 8888)"
    )
    chat_parser.add_argument(
        "--rest-port", "-r", type=int, default=8080, help="Server REST API port (default: 8080)"
    )
    chat_parser.add_argument(
        "--lucid",
        "-l",
        dest="debug",
        action="store_true",
        help="Lucid dream mode - enable debug logging",
    )
    chat_parser.add_argument("--debug", action="store_true", help=argparse.SUPPRESS)  # Hidden alias

    # ops command - open Operator console (ops TUI)
    ops_parser = subparsers.add_parser(
        "ops",
        help="Open Operator console (monitor and manage)",
        description="""Connect to the Tlon server through the Operator console.

Professional operations console for monitoring and managing agents.""",
    )
    ops_parser.add_argument("--host", default="127.0.0.1", help="Server host (default: 127.0.0.1)")
    ops_parser.add_argument(
        "--port", "-p", type=int, default=8888, help="Server WebSocket port (default: 8888)"
    )
    ops_parser.add_argument(
        "--rest-port", "-r", type=int, default=8080, help="Server REST API port (default: 8080)"
    )
    ops_parser.add_argument(
        "--lucid",
        "-l",
        dest="debug",
        action="store_true",
        help="Lucid dream mode - enable debug logging",
    )
    ops_parser.add_argument("--debug", action="store_true", help=argparse.SUPPRESS)  # Hidden alias

    # intrude command - intrude into reality (legacy, now same as ops)
    intrude_parser = subparsers.add_parser(
        "intrude",
        aliases=["tui"],  # Backward compatibility
        help="Intrude into reality (legacy alias for 'ops')",
        description="""Legacy command - use 'tlon ops' or 'tlon chat' instead.

Defaults to the Operator console for backward compatibility.""",
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
Through the mirror, you shape the forms that will inhabit Tlon.""",
    )
    mirror_parser.add_argument(
        "--lucid",
        "-l",
        dest="debug",
        action="store_true",
        help="Lucid dream mode - enable debug logging",
    )
    mirror_parser.add_argument(
        "--debug", action="store_true", help=argparse.SUPPRESS
    )  # Hidden alias

    return parser


async def main_async(args: Namespace | None = None) -> int:
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
    is_debug = getattr(args, "debug", False) or get_settings().debug

    configure_logging(
        log_level="DEBUG" if is_debug else get_settings().log_level,
        debug=is_debug,
    )

    # Route to appropriate command
    cmd = args.command

    # dream / server (start server)
    if cmd in ("dream", "server"):
        return await run_dream(args)

    # chat (Congress TUI)
    elif cmd == "chat":
        return await run_chat(args)

    # ops / intrude / tui (Operator TUI)
    elif cmd in ("ops", "intrude", "tui"):
        return await run_ops(args)

    # mirror / wizard (configure agents)
    elif cmd in ("mirror", "wizard"):
        return await run_mirror(args)

    else:
        # No command - show help
        print("""
🐙 Tlon - 一个由观念构建的、正在入侵现实的宇宙

Commands:
  dream       维持梦境（启动 Server）
  chat        进入议会（聊天界面）
  ops         打开运维控制台
  mirror      镜中造物（配置 Agent）

Usage:
  tlon dream                    # 启动服务
  tlon dream --lucid            # Debug 模式
  tlon chat                     # 聊天界面
  tlon ops                      # 运维控制台
  tlon mirror                   # 配置向导

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

    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\n\n入侵被中断...")
        return 130


if __name__ == "__main__":
    sys.exit(main())
