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

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import TYPE_CHECKING

from tlon.app.cli.commands import run_dream, run_intrude, run_mirror
from tlon.base.logger import configure_logging
from tlon.base.settings import settings

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
    subparsers.add_parser(
        "mirror",
        aliases=["wizard"],  # Backward compatibility
        help="Create in the mirror (configure agents)",
        description="""Interactive configuration wizard for creating and managing agents.

From "The Mirror and the Mask": The mirror reveals and creates.
Through the mirror, you shape the forms that will inhabit Tlon.""",
    )

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
    is_debug = getattr(args, "debug", False) or settings.debug
    configure_logging(
        log_level="DEBUG" if is_debug else settings.log_level,
        debug=is_debug,
    )

    # Route to appropriate command
    cmd = args.command

    # dream / server (start server)
    if cmd in ("dream", "server"):
        return await run_dream(args)

    # intrude / tui (connect interface)
    elif cmd in ("intrude", "tui"):
        return await run_intrude(args)

    # mirror / wizard (configure agents)
    elif cmd in ("mirror", "wizard"):
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

    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\n\n入侵被中断...")
        return 130


if __name__ == "__main__":
    sys.exit(main())
