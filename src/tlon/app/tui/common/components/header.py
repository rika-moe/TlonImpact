"""Header component for Tlon TUI."""

from textual.widgets import Static


class Header(Static):
    """Header component displaying title and status."""

    DEFAULT_CSS = """
    Header {
        height: 3;
        background: $surface-darken-1;
        color: $text;
        content-align: center middle;
        text-style: bold;
    }
    """

    def __init__(self, title: str = "Tlon", subtitle: str = "") -> None:
        """Initialize header.

        Args:
            title: Main title to display
            subtitle: Optional subtitle
        """
        super().__init__()
        self.title = title
        self.subtitle = subtitle

    def compose(self):
        """Compose the header."""
        content = f"{self.title}"
        if self.subtitle:
            content += f" - {self.subtitle}"
        self.update(content)

    def update_title(self, title: str, subtitle: str = "") -> None:
        """Update the header title.

        Args:
            title: New title
            subtitle: New subtitle
        """
        self.title = title
        if subtitle:
            self.subtitle = subtitle
        content = f"{self.title}"
        if self.subtitle:
            content += f" - {self.subtitle}"
        self.update(content)
