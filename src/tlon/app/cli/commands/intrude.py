"""Intrude command - connect to Tlon through UI.

Tlon invades reality through the chosen interface.
"""

from __future__ import annotations

import asyncio
import signal
from typing import TYPE_CHECKING

from tlon.base.logger import configure_logging, get_logger
from tlon.base.settings import settings

if TYPE_CHECKING:
    from argparse import Namespace

logger = get_logger(__name__)


async def run_intrude(args: Namespace) -> int:
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

        # Runtime import - only load TUI when needed
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
                    host=args.host,
                    port=args.port,
                    rest_port=getattr(args, "rest_port", 8080),
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
