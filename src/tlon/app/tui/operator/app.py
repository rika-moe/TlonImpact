"""Tlon Operator TUI Application.

Main application class for the Operator TUI with multi-view framework,
IDE-style layout, and keyboard navigation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import Label, Static

from tlon.app.tui.operator.components.inspector import InspectorPanel
from tlon.app.tui.operator.components.navigation import NavigationBar
from tlon.app.tui.operator.components.sidebar import MiniEventStream, Sidebar
from tlon.app.tui.operator.views.agents import AgentsView
from tlon.app.tui.operator.views.config import ConfigView
from tlon.app.tui.operator.views.events import EventsView
from tlon.app.tui.operator.views.logs import LogsView
from tlon.app.tui.operator.views.traces import TracesView

if TYPE_CHECKING:
    from textual.widget import Widget


class HelpScreen(ModalScreen[None]):
    """Help screen with keyboard shortcuts."""

    DEFAULT_CSS = """
    HelpScreen {
        align: center middle;
    }
    HelpScreen .help-container {
        width: 60;
        height: auto;
        max-height: 40;
        background: $surface;
        border: solid $primary;
        padding: 1 2;
    }
    HelpScreen .help-title {
        text-style: bold;
        text-align: center;
        color: $text-accent;
        margin-bottom: 1;
    }
    HelpScreen .help-section {
        margin: 1 0;
    }
    HelpScreen .help-section-title {
        text-style: bold;
        color: $text-accent;
    }
    HelpScreen .help-row {
        height: 1;
        margin: 0;
    }
    HelpScreen .help-key {
        width: 12;
        color: $text-muted;
    }
    HelpScreen .help-desc {
        width: 1fr;
    }
    HelpScreen .help-footer {
        text-align: center;
        color: $text-muted;
        margin-top: 1;
    }
    """

    def compose(self) -> ComposeResult:
        """Compose the help screen."""
        with Vertical(classes="help-container"):
            yield Label("Keyboard Shortcuts", classes="help-title")

            with Vertical(classes="help-section"):
                yield Label("View Navigation", classes="help-section-title")
                yield Static("1 - Agents view", classes="help-row")
                yield Static("2 - Events view", classes="help-row")
                yield Static("3 - Logs view", classes="help-row")
                yield Static("4 - Traces view", classes="help-row")
                yield Static("5 - Config view", classes="help-row")

            with Vertical(classes="help-section"):
                yield Label("Agents View", classes="help-section-title")
                yield Static("n - New Agent", classes="help-row")
                yield Static("e - Edit Agent", classes="help-row")
                yield Static("d - Delete Agent", classes="help-row")
                yield Static("s - Stop/Start Agent", classes="help-row")
                yield Static("l - View Logs", classes="help-row")
                yield Static("Space - Expand/Collapse", classes="help-row")

            with Vertical(classes="help-section"):
                yield Label("General", classes="help-section-title")
                yield Static("r - Refresh current view", classes="help-row")
                yield Static("q / Ctrl+C - Quit", classes="help-row")
                yield Static("? - Show this help", classes="help-row")

            yield Label("Press any key to close", classes="help-footer")

    def on_key(self) -> None:
        """Close on any key press."""
        self.dismiss()


class OperatorApp(App[None]):
    """Tlon Operator TUI Application.

    IDE-style multi-view framework for managing and monitoring
    Tlon Agent clusters.
    """

    CSS = """
    Screen {
        layout: vertical;
    }
    #main-content {
        width: 60%;
        height: 100%;
        background: $surface;
    }
    #content-area {
        height: 1fr;
    }
    #bottom-area {
        height: auto;
    }
    """

    BINDINGS = [
        Binding("1", "switch_view('agents')", "Agents", show=True),
        Binding("2", "switch_view('events')", "Events", show=True),
        Binding("3", "switch_view('logs')", "Logs", show=True),
        Binding("4", "switch_view('traces')", "Traces", show=True),
        Binding("5", "switch_view('config')", "Config", show=True),
        Binding("r", "refresh_view", "Refresh", show=True),
        Binding("q", "quit", "Quit", show=True),
        Binding("ctrl+c", "quit", "Quit", show=False),
        Binding("?", "show_help", "Help", show=True),
    ]

    # Reactive current view
    current_view_id: reactive[str] = reactive("agents")

    def __init__(
        self,
        host: str = "127.0.0.1",
        ws_port: int = 8888,
        rest_port: int = 8080,
        **kwargs,
    ) -> None:
        """Initialize the Operator app.

        Args:
            host: Server host address
            ws_port: WebSocket port
            rest_port: REST API port
            **kwargs: Additional arguments passed to App
        """
        super().__init__(**kwargs)
        self.host = host
        self.ws_port = ws_port
        self.rest_port = rest_port
        self._views: dict[str, Widget] = {}

    def compose(self) -> ComposeResult:
        """Compose the application layout.

        Yields:
            ComposeResult: The composed widgets
        """
        # Top navigation bar
        yield NavigationBar(id="navigation")

        # Main layout: Sidebar | Content | Inspector
        with Horizontal(id="content-area"):
            yield Sidebar(id="sidebar")

            with Vertical(id="main-content"):
                # Views will be mounted here
                yield AgentsView(id="view-agents")
                yield EventsView(id="view-events")
                yield LogsView(id="view-logs")
                yield TracesView(id="view-traces")
                yield ConfigView(id="view-config")

            yield InspectorPanel(id="inspector")

        # Bottom mini event stream
        yield MiniEventStream(id="mini-events")

    def on_mount(self) -> None:
        """Handle app mount event."""
        # Store references to views
        self._views = {
            "agents": self.query_one("#view-agents", AgentsView),
            "events": self.query_one("#view-events", EventsView),
            "logs": self.query_one("#view-logs", LogsView),
            "traces": self.query_one("#view-traces", TracesView),
            "config": self.query_one("#view-config", ConfigView),
        }

        # Show initial view
        self._show_view("agents")

    def on_navigation_bar_tab_changed(self, event: NavigationBar.TabChanged) -> None:
        """Handle tab change from navigation bar.

        Args:
            event: The tab changed event
        """
        self._show_view(event.tab_id)

    def watch_current_view_id(self, view_id: str) -> None:
        """Watch for view ID changes.

        Args:
            view_id: The new view ID
        """
        # Update navigation bar
        nav = self.query_one("#navigation", NavigationBar)
        nav.current_tab = view_id

    def _show_view(self, view_id: str) -> None:
        """Show a specific view and hide others.

        Args:
            view_id: The view ID to show
        """
        if view_id not in self._views:
            return

        self.current_view_id = view_id

        for vid, view in self._views.items():
            if vid == view_id:
                view.display = True
                view.remove_class("hidden")
            else:
                view.display = False
                view.add_class("hidden")

    def action_switch_view(self, view_id: str) -> None:
        """Switch to a specific view.

        Args:
            view_id: The view ID to switch to
        """
        self._show_view(view_id)

    async def action_refresh_view(self) -> None:
        """Refresh the current view."""
        current_view = self._views.get(self.current_view_id)
        if current_view and hasattr(current_view, "refresh_view"):
            await current_view.refresh_view()

    def action_show_help(self) -> None:
        """Show the help screen."""
        self.push_screen(HelpScreen())
