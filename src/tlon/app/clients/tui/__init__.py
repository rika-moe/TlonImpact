"""TUI Client module for Tlon.

This module provides the Terminal UI client for interacting with the Tlon server.

Usage:
    >>> from tlon.app.clients.tui import run_tui_client
    >>> await run_tui_client(host="localhost", port=8888)

Or use the TUIClient class directly:
    >>> from tlon.app.clients.tui import TUIClient
    >>> client = TUIClient()
    >>> await client.run()
"""

# New SDK-based TUI client
from .client import TUIClient, run_tui_client

# Legacy TUI client (for backward compatibility)
from .tui_simple import SimpleTUIClient, run_tui_simple

__all__ = [
    # New SDK-based client
    "TUIClient",
    "run_tui_client",
    # Legacy client
    "SimpleTUIClient",
    "run_tui_simple",
]
