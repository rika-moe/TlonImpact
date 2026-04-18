"""Navigation component for Operator TUI.

Provides the top tab navigation bar with Agents, Events, Logs, Traces, and Config tabs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.containers import Horizontal
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Button, Label

if TYPE_CHECKING:
    from textual.app import ComposeResult


class NavigationBar(Horizontal):
    """Top navigation bar with tab buttons.

    Provides tab switching for the five main views:
    - Agents
    - Events
    - Logs
    - Traces
    - Config
    """

    DEFAULT_CSS = """
    NavigationBar {
        height: 3;
        background: $surface-darken-1;
        border-bottom: solid $primary;
        padding: 0 1;
    }
    NavigationBar .title {
        width: auto;
        content-align: center middle;
        text-style: bold;
        color: $text-accent;
        padding: 0 2;
    }
    NavigationBar .tabs {
        width: 1fr;
        height: 100%;
        align: center middle;
    }
    NavigationBar .tab {
        min-width: 12;
        margin: 0 1;
    }
    NavigationBar .tab.active {
        background: $primary;
        color: $text;
        text-style: bold;
    }
    """

    # Reactive current tab
    current_tab: reactive[str] = reactive("agents")

    # Tab definitions
    TABS = [
        ("agents", "Agents", "1"),
        ("events", "Events", "2"),
        ("logs", "Logs", "3"),
        ("traces", "Traces", "4"),
        ("config", "Config", "5"),
    ]

    def __init__(self, **kwargs) -> None:
        """Initialize the navigation bar."""
        super().__init__(**kwargs)

    def compose(self) -> ComposeResult:
        """Compose the navigation bar.

        Yields:
            ComposeResult: The composed widgets
        """
        yield Label("Tlon Operator", classes="title")

        with Horizontal(classes="tabs"):
            for tab_id, label, _ in self.TABS:
                btn = Button(label, id=f"tab-{tab_id}", classes="tab")
                if tab_id == self.current_tab:
                    btn.add_class("active")
                yield btn

    def watch_current_tab(self, tab: str) -> None:
        """Watch for tab changes and update button styles.

        Args:
            tab: The new active tab ID
        """
        for tab_id, _, _ in self.TABS:
            button = self.query_one(f"#tab-{tab_id}", Button)
            if tab_id == tab:
                button.add_class("active")
            else:
                button.remove_class("active")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button press events.

        Args:
            event: The button press event
        """
        button_id = event.button.id
        if button_id and button_id.startswith("tab-"):
            tab_id = button_id[4:]  # Remove "tab-" prefix
            self.current_tab = tab_id
            self.post_message(self.TabChanged(tab_id))

    def switch_to_tab(self, tab_id: str) -> None:
        """Switch to a specific tab.

        Args:
            tab_id: The tab ID to switch to
        """
        valid_tabs = [t[0] for t in self.TABS]
        if tab_id in valid_tabs:
            self.current_tab = tab_id
            self.post_message(self.TabChanged(tab_id))

    class TabChanged(Message):
        """Message sent when the active tab changes."""

        def __init__(self, tab_id: str) -> None:
            """Initialize the message.

            Args:
                tab_id: The new active tab ID
            """
            super().__init__()
            self.tab_id = tab_id
