"""Mirror command - configure agents.

From "The Mirror and the Mask": The mirror reveals and creates.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from argparse import Namespace


async def run_mirror(args: Namespace) -> int:
    """Run the mirror command (configure agents).

    From "The Mirror and the Mask": The mirror reveals and creates.

    Args:
        args: Parsed arguments (unused, kept for interface consistency)

    Returns:
        Exit code
    """
    # Runtime import - only load Wizard when needed
    from tlon.app.wizard.wizard import Wizard

    try:
        wizard = Wizard()
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, wizard.run)
        return 0
    except Exception as e:
        print(f"\n错误: {e}")
        return 1
