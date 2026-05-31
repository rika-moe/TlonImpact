"""Dream command - start the Tlon server.

From "The Circular Ruins": The dreamer creates reality through dreams.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tlon.base.settings import get_settings

if TYPE_CHECKING:
    from argparse import Namespace


async def run_dream(args: Namespace) -> int:
    """Run the dream command (start server).

    From "The Circular Ruins": The dreamer creates reality through dreams.

    Args:
        args: Parsed arguments

    Returns:
        Exit code
    """
    from tlon.app.server.serverapp import run_server

    is_lucid = getattr(args, "debug", False)
    log_level = "DEBUG" if is_lucid else get_settings().log_level

    return await run_server(
        host=args.host,
        rest_port=args.rest_port,
        ws_port=args.ws_port,
        log_level=log_level,
        debug=is_lucid,
    )
