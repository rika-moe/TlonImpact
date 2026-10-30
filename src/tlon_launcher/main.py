"""Tlon CLI - Command line interface.

Provides commands (Borgesian metaphor):
- tlon dream    : Maintain the dream (start server) - from "The Circular Ruins"

Usage:
    $ tlon dream            # Start server on the default port (8080)
    $ tlon dream --lucid    # Debug mode (lucid dream)
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

    else:
        # No command - show help
        print("""
🐙 Tlon - 一个由观念构建的、正在入侵现实的宇宙

Commands:
  dream       维持梦境（启动 Server，空社会首启自动初始化标准团队；WebUI 托管于 /ui/）

Usage:
  tlon dream                    # 启动服务（启动 banner 显示 WebUI 地址）
  tlon dream --lucid            # Debug 模式

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
