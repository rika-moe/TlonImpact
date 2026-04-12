"""Tlon Textual TUI Client.

Provides a Textual-based TUI interface for connecting to Tlon server.
This is a wrapper around the Congress TUI application.
"""

from __future__ import annotations

from tlon.app.tui.congress import CongressApp


class TlonTextualApp(CongressApp):
    """Textual TUI Client for Tlon.

    This is a compatibility wrapper around CongressApp that provides
    the expected interface for the intrude command.

    Example:
        >>> app = TlonTextualApp(host="localhost", ws_port=8888, rest_port=8080)
        >>> await app.run_async()
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        ws_port: int = 8888,
        rest_port: int = 8080,
    ):
        """Initialize the Textual TUI client.

        Args:
            host: Server host address
            ws_port: WebSocket server port
            rest_port: REST API server port
        """
        super().__init__(
            host=host,
            ws_port=ws_port,
            rest_port=rest_port,
        )
