"""Ops command - Operator TUI (operations console).

Tlon Operator - Professional operations console for engineers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tlon.base.logger import configure_logging, get_logger

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
    if is_lucid:
        configure_logging(log_level="DEBUG", debug=True)
    else:
        configure_logging(silent=True)

    # Import here to avoid loading textual unless needed
    try:
        from tlon.app.clients.tui.operator.app import OperatorApp
    except ImportError as e:
        logger.error(f"Failed to import Operator TUI: {e}")
        print("\n❌ Operator TUI not available. Is textual installed?")
        print("   uv add textual")
        return 1

    app = OperatorApp(
        host=args.host,
        ws_port=args.port,
        rest_port=args.rest_port,
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
