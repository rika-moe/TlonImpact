"""Status bar component for Tlon TUI."""

from textual.widgets import Static


class StatusBar(Static):
    """Status bar component displaying connection and system status."""

    DEFAULT_CSS = """
    StatusBar {
        height: 1;
        background: $surface-darken-2;
        color: $text-muted;
        content-align: left middle;
        padding: 0 1;
    }
    """

    def __init__(self) -> None:
        """Initialize status bar."""
        super().__init__()
        self.connection_status = "disconnected"
        self.message = ""

    def on_mount(self) -> None:
        """Mount the status bar."""
        self.update_status("disconnected", "Ready")

    def update_status(self, status: str, message: str = "") -> None:
        """Update the status bar.

        Args:
            status: Connection status (connected, disconnected, reconnecting, error)
            message: Optional status message
        """
        self.connection_status = status
        self.message = message

        # Status indicator colors
        indicators = {
            "connected": "🟢",
            "disconnected": "⚪",
            "reconnecting": "🟡",
            "error": "🔴",
        }
        indicator = indicators.get(status, "⚪")

        content = f"{indicator} {status.upper()}"
        if message:
            content += f" | {message}"

        self.update(content)

    def set_connected(self, message: str = "Connected") -> None:
        """Set status to connected.

        Args:
            message: Optional connection message
        """
        self.update_status("connected", message)

    def set_disconnected(self, message: str = "Disconnected") -> None:
        """Set status to disconnected.

        Args:
            message: Optional disconnection message
        """
        self.update_status("disconnected", message)

    def set_reconnecting(self, message: str = "Reconnecting...") -> None:
        """Set status to reconnecting.

        Args:
            message: Optional reconnection message
        """
        self.update_status("reconnecting", message)
