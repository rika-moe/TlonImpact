"""CLI commands for Tlon.

This module provides the command implementations for the Tlon CLI:
- dream: Start the server (maintain the dream)
- intrude: Connect to the server (invade reality)
- mirror: Configure agents (create in the mirror)
"""

from .chat import run_chat
from .dream import run_dream
from .intrude import run_intrude
from .mirror import run_mirror
from .ops import run_ops

__all__ = [
    "run_chat",
    "run_dream",
    "run_intrude",
    "run_mirror",
    "run_ops",
]
