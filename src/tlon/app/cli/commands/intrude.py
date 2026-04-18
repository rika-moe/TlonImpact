"""Intrude command - connect to Tlon through UI.

Tlon invades reality through the chosen interface.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tlon.base.logger import configure_logging, get_logger

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
    if is_lucid:
        configure_logging(log_level="DEBUG", debug=True)
    else:
        configure_logging(silent=True)

    interface = getattr(args, "interface", "tui")

    if interface == "tui":
        # Runtime import - only load TUI when needed
        from tlon.app.clients.textual import TlonTextualApp

        app = TlonTextualApp(
            host=args.host,
            ws_port=args.port,
            rest_port=getattr(args, "rest_port", 8080),
        )

        try:
            return await app.run_async()
        except KeyboardInterrupt:
            print("\n\n入侵被中断...")
            return 130

    else:
        print(f"\n❌ 未知的入侵点: {interface}")
        return 1
