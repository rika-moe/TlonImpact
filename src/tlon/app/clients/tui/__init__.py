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

from .client import TUIClient, run_tui_client

__all__ = [
    "TUIClient",
    "run_tui_client",
]
