"""Chat command - Congress TUI (chat interface).

Tlon Congress - Chat with agents through the terminal.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tlon.base.logger import configure_logging, get_logger
from tlon.base.settings import settings

if TYPE_CHECKING:
    from argparse import Namespace

logger = get_logger(__name__)


async def run_chat(args: Namespace) -> int:
    """Run the chat command (Congress TUI).

    Congress - Chat interface for talking with agents.

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
        from tlon.app.tui.congress.app import CongressApp
    except ImportError as e:
        logger.error(f"Failed to import Congress TUI: {e}")
        print("\n❌ Congress TUI not available. Is textual installed?")
        print("   uv add textual")
        return 1

    app = CongressApp(
        host=args.host,
        ws_port=args.port,
        rest_port=getattr(args, "rest_port", 8080),
    )

    try:
        return await app.run_async()
    except KeyboardInterrupt:
        print("\n\n对话已结束...")
        return 130
    except Exception as e:
        logger.error(f"Congress TUI error: {e}")
        print(f"\n❌ Congress TUI error: {e}")
        return 1
