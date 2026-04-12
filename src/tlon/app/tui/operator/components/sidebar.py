"""Sidebar component for Operator TUI.

Provides the left sidebar with agent tree and mini event stream.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.containers import Vertical
from textual.widgets import Label, Static

if TYPE_CHECKING:
    from textual.app import ComposeResult


class Sidebar(Vertical):
    """Left sidebar with agent tree.

    Placeholder implementation for the sidebar as specified
    in the design document.
    """

    DEFAULT_CSS = """
    Sidebar {
        width: 20%;
        height: 100%;
        background: $surface-darken-1;
        border-right: solid $primary;
        padding: 1;
    }
    Sidebar .header {
        text-style: bold;
        color: $text-accent;
        margin-bottom: 1;
    }
    Sidebar .content {
        height: 1fr;
        overflow: auto;
    }
    """

    def __init__(self, **kwargs) -> None:
        """Initialize the sidebar."""
        super().__init__(**kwargs)

    def compose(self) -> ComposeResult:
        """Compose the sidebar.

        Yields:
            ComposeResult: The composed widgets
        """
        yield Label("Agents", classes="header")
        yield Static(
            "📁 production\n"
            "  🤖 dev-001  ●\n"
            "  🤖 pm-001   ○\n"
            "📁 development\n"
            "  🤖 dev-local\n"
            "📁 system\n"
            "  🔄 scheduler\n",
            classes="content",
        )


class MiniEventStream(Vertical):
    """Bottom mini event stream.

    Placeholder implementation for the mini event stream as specified
    in the design document.
    """

    DEFAULT_CSS = """
    MiniEventStream {
        height: 5;
        background: $surface-darken-2;
        border-top: solid $primary;
        padding: 0 1;
    }
    MiniEventStream .header {
        height: 1;
        text-style: bold;
        color: $text-accent;
    }
    MiniEventStream .content {
        height: 3;
        overflow: auto;
    }
    """

    def __init__(self, **kwargs) -> None:
        """Initialize the mini event stream."""
        super().__init__(**kwargs)

    def compose(self) -> ComposeResult:
        """Compose the mini event stream.

        Yields:
            ComposeResult: The composed widgets
        """
        yield Label("Event Stream", classes="header")
        yield Static(
            "16:32:01 [INFO] Task completed | 16:31:58 [DEBUG] Analysis complete",
            classes="content",
        )
