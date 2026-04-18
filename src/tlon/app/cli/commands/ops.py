"""Ops command - Operator TUI (operations console).

Tlon Operator - Professional operations console for engineers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tlon.base.logger import configure_logging, get_logger
from tlon.base.settings import settings

if TYPE_CHECKING:
    from argparse import Namespace

logger = get_logger(__name__)


async def run_ops(args: Namespace) -> int:
    """Run the ops command (Operator TUI).

    Operator - Professional operations console for engineers.

    Args:
        args: Parsed arguments

    Returns:
        Exit code
    """
    is_lucid = getattr(args, "debug", False)
    log_level = "DEBUG" if is_lucid else settings.log_level
    configure_logging(log_level=log_level, debug=is_lucid)

    # Import here to avoid loading textual unless needed
    try:
        from tlon.app.clients.operator import OperatorApp
    except ImportError as e:
        logger.error(f"Failed to import Operator TUI: {e}")
        print("\n❌ Operator TUI not available. Is textual installed?")
        print("   uv add textual")
        return 1

    app = OperatorApp(
        host=args.host,
        ws_port=args.port,
        rest_port=args.rest_port,
        initial_view=getattr(args, "view", "agents"),
        initial_agent=getattr(args, "agent", None),
    )

    try:
        return await app.run_async()
    except KeyboardInterrupt:
        print("\n\n运维控制台已关闭...")
        return 130
    except Exception as e:
        logger.error(f"Operator TUI error: {e}")
        print(f"\n❌ Operator TUI error: {e}")
        return 1
