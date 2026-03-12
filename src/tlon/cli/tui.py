"""TUI Client entry point.

This module re-exports the simple TUI implementation as the primary interface.
The simple TUI provides:
- Stream-based console output (supports copy-paste)
- Three modes: chat, event, agent (switchable via /mode command)
- Input line stays at bottom using prompt-toolkit

Legacy versions (tui_legacy.py, tui_textual.py) have been removed.
"""

from .tui_simple import run_tui_simple, SimpleTUIClient

__all__ = ["run_tui_simple", "SimpleTUIClient"]
