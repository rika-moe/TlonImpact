"""Tlon CLI - Command line interface.

Provides commands (Borgesian metaphor):
- tlon dream    : Maintain the dream (start server) - from "The Circular Ruins"
- tlon ops      : Open Operator console (monitor and manage)

Usage:
    $ tlon dream            # Start server on the default port (8080)
    $ tlon dream --lucid    # Debug mode (lucid dream)
    $ tlon ops              # Connect operator TUI
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from io import TextIOWrapper
from typing import TYPE_CHECKING

from tlon.base.logger import configure_logging
from tlon.base.paths import TlonPaths
from tlon.base.settings import Settings, load_process_env
from tlon.base.version import VERSION
from tlon_launcher.commands.dream import run_dream
from tlon_launcher.commands.ops import run_ops
from tlon_launcher.commands.telegram import run_telegram

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
  %(prog)s ops              Open Operator console (ops TUI)
  %(prog)s telegram         Run the Telegram bot client
        """,
    )

    # Global options (apply to all commands including embedded mode)
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode (verbose logging)")

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # dream command - maintain the dream (start server)
    dream_parser = subparsers.add_parser(
        "dream",
        help="Maintain the dream (start server daemon)",
        description="""Start the Tlon server with the HTTP API (REST + SSE).

From "The Circular Ruins": The dreamer creates reality through dreams.
The server is the dream that sustains the Tlon universe.""",
    )
    dream_parser.add_argument(
        "--host",
        default="127.0.0.1",
        choices=("127.0.0.1", "localhost", "::1"),
        help="Loopback host to bind to (default: 127.0.0.1)",
    )
    dream_parser.add_argument(
        "--rest-port", type=int, default=8080, help="Port for the HTTP API (default: 8080)"
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

    # ops command - open Operator console (ops TUI)
    ops_parser = subparsers.add_parser(
        "ops",
        help="Open Operator console (monitor and manage)",
        description="""Connect to the Tlon server through the Operator console.

Professional operations console for monitoring and managing agents.""",
    )
    ops_parser.add_argument("--host", default="127.0.0.1", help="Server host (default: 127.0.0.1)")
    ops_parser.add_argument(
        "--username",
        "-u",
        default=None,
        help="Your mail identity (default: guest, env TLON_USERNAME)",
    )
    ops_parser.add_argument(
        "--rest-port", "-r", type=int, default=8080, help="Server HTTP API port (default: 8080)"
    )
    ops_parser.add_argument(
        "--lucid",
        "-l",
        dest="debug",
        action="store_true",
        help="Lucid dream mode - enable debug logging",
    )
    ops_parser.add_argument("--debug", action="store_true", help=argparse.SUPPRESS)  # Hidden alias

    # telegram command - Telegram bot client (mail on the phone)
    telegram_parser = subparsers.add_parser(
        "telegram",
        help="Run the Telegram bot client (requires the telegram extra)",
        description="""Run the Telegram bot: incoming mail is pushed to Telegram,
Telegram replies go back into the mail network (docs/design/telegram.md).
Requires: uv sync --extra telegram + TELEGRAM_BOT_TOKEN / TELEGRAM_ALLOWED_USERS
in ~/.tlon/.env.""",
    )
    telegram_parser.add_argument(
        "--host", default="127.0.0.1", help="Server host (default: 127.0.0.1)"
    )
    telegram_parser.add_argument(
        "--username",
        "-u",
        default=None,
        help="Your mail identity (default: guest, env TLON_USERNAME)",
    )
    telegram_parser.add_argument(
        "--rest-port", "-r", type=int, default=8080, help="Server HTTP API port (default: 8080)"
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

    # 进程入口纪律（ADR 0019）：TlonPaths / Settings 各构造一次并显式
    # 往下传；.env 回退加载必须在任何 env 读取（Settings.from_env）之前。
    tlon_paths = TlonPaths()
    load_process_env(tlon_paths)
    settings = Settings.from_env()

    # Configure logging - use --debug flag if provided
    is_debug = getattr(args, "debug", False) or settings.debug

    configure_logging(
        log_level="DEBUG" if is_debug else settings.log_level,
        debug=is_debug,
    )

    # Route to appropriate command
    cmd = args.command

    if cmd == "dream":
        return await run_dream(args, tlon_paths, settings)

    elif cmd == "ops":
        return await run_ops(args)

    elif cmd == "telegram":
        return await run_telegram(args, settings)

    else:
        # No command - show help
        print("""
🐙 Tlon - 一个由观念构建的、正在入侵现实的宇宙

Commands:
  dream       维持梦境（启动 Server，空社会首启自动初始化标准团队；WebUI 托管于 /ui/）
  ops         打开运维控制台（含邮件视图与成员/原型管理）
  telegram    Telegram bot 客户端（手机收发邮件，需 telegram extra）

Usage:
  tlon dream                    # 启动服务（启动 banner 显示 WebUI 地址）
  tlon dream --lucid            # Debug 模式
  tlon ops                      # 运维控制台
  tlon telegram                 # Telegram bot（手机通道）

Run 'tlon --help' for more information.
""")
        return 0


def main() -> int:
    """Main entry point.

    Returns:
        Exit code
    """
    # CLI output is UTF-8 even when redirected on a non-UTF-8 Windows code page.
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors=stream.errors)

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
