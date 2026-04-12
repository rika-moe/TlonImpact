"""Inspector panel component for Operator TUI.

Provides the right panel with metrics and status information.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.containers import Vertical
from textual.widgets import Label, Static

if TYPE_CHECKING:
    from textual.app import ComposeResult


class InspectorPanel(Vertical):
    """Right inspector panel with metrics and status.

    Placeholder implementation for the inspector panel as specified
    in the design document.
    """

    DEFAULT_CSS = """
    InspectorPanel {
        width: 20%;
        height: 100%;
        background: $surface-darken-1;
        border-left: solid $primary;
        padding: 1;
    }
    InspectorPanel .header {
        text-style: bold;
        color: $text-accent;
        margin-bottom: 1;
    }
    InspectorPanel .content {
        height: 1fr;
        overflow: auto;
    }
    InspectorPanel .metric {
        margin: 1 0;
    }
    InspectorPanel .metric-label {
        color: $text-muted;
    }
    InspectorPanel .metric-value {
        text-style: bold;
    }
    """

    def __init__(self, **kwargs) -> None:
        """Initialize the inspector panel."""
        super().__init__(**kwargs)

    def compose(self) -> ComposeResult:
        """Compose the inspector panel.

        Yields:
            ComposeResult: The composed widgets
        """
        yield Label("Inspector", classes="header")
        yield Static(
            "[No agent selected]\n\n"
            "Status: --\n"
            "Uptime: --\n"
            "Tasks: --\n"
            "Errors: --\n\n"
            "CPU: --\n"
            "MEM: --\n"
            "WS: --",
            classes="content",
        )

    def update_for_agent(self, agent_id: str | None) -> None:
        """Update the inspector for a selected agent.

        Args:
            agent_id: The selected agent ID or None
        """
        content = self.query_one(".content", Static)
        if agent_id:
            content.update(
                f"{agent_id}\n\n"
                "Status: ● healthy\n"
                "Uptime: 2d 4h 32m\n"
                "Tasks: 142 total\n"
                "Errors: 3 (0.02%)\n\n"
                "CPU: 12%\n"
                "MEM: 256MB\n"
                "WS: ● connected"
            )
        else:
            content.update(
                "[No agent selected]\n\n"
                "Status: --\n"
                "Uptime: --\n"
                "Tasks: --\n"
                "Errors: --\n\n"
                "CPU: --\n"
                "MEM: --\n"
                "WS: --"
            )
