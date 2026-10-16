"""Dream command - start the Tlon server.

From "The Circular Ruins": The dreamer creates reality through dreams.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from argparse import Namespace

    from tlon.base.paths import TlonPaths
    from tlon.base.settings import Settings


async def run_dream(args: Namespace, tlon_paths: TlonPaths, settings: Settings) -> int:
    """Run the dream command (start server).

    From "The Circular Ruins": The dreamer creates reality through dreams.

    Args:
        args: Parsed arguments
        tlon_paths: Process path resolver, constructed once at the entry
            point and handed down (ADR 0019)
        settings: Process settings snapshot, likewise

    Returns:
        Exit code
    """
    from tlon_server.serverapp import run_server

    is_lucid = getattr(args, "debug", False)
    log_level = "DEBUG" if is_lucid else settings.log_level

    return await run_server(
        host=args.host,
        rest_port=args.rest_port,
        log_level=log_level,
        debug=is_lucid,
        tlon_paths=tlon_paths,
        settings=settings,
    )
