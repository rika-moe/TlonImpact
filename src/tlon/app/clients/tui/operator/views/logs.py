"""Logs view for Operator TUI.

Displays real-time logs from agents with:
- Live log stream with level-based color highlighting
- Agent selector filter
- Keyword grep filtering
- Auto-scroll and pause controls
- Keyboard shortcuts
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import Button, Input, Label, RichLog, Select, Static

from tlon.app.clients.tui.operator.views.base import BaseView

if TYPE_CHECKING:
    from textual.app import ComposeResult


@dataclass
class LogEntry:
    """Single log entry."""

    timestamp: datetime
    agent: str
    level: str
    message: str


class LogsView(BaseView):
    """Logs view - displays real-time agent logs.

    Provides a complete interface for viewing and filtering agent logs:
    - Live log stream with color-coded levels
    - Agent and keyword filtering
    - Auto-scroll with pause capability
    """

    DEFAULT_CSS = """
    LogsView {
        layout: vertical;
        padding: 1;
    }
    LogsView .logs-header {
        height: auto;
        margin-bottom: 1;
    }
    LogsView .logs-title {
        width: auto;
        text-style: bold;
        color: $text-accent;
    }
    LogsView .logs-toolbar {
        height: auto;
        align: left middle;
        margin-bottom: 1;
    }
    LogsView .logs-toolbar Select {
        width: 20;
        margin-right: 1;
    }
    LogsView .logs-toolbar Input {
        width: 30;
        margin-right: 1;
    }
    LogsView .logs-toolbar Button {
        margin: 0 0 0 1;
    }
    LogsView .logs-status {
        width: auto;
        color: $text-muted;
    }
    LogsView .logs-container {
        height: 1fr;
        border: solid $primary;
    }
    LogsView .logs-container RichLog {
        height: 1fr;
    }
    """

    BINDINGS = [
        Binding("slash", "focus_filter", "Filter", show=True),
        Binding("g", "jump_latest", "Latest", show=True),
        Binding("p", "toggle_pause", "Pause", show=True),
    ]

    # Reactive state
    is_paused: reactive[bool] = reactive(False)
    current_agent: reactive[str] = reactive("all")
    filter_text: reactive[str] = reactive("")

    def __init__(self, **kwargs) -> None:
        """Initialize the Logs view."""
        super().__init__(view_id="logs", **kwargs)
        self._all_logs: list[LogEntry] = []
        self._timer = None

    def render_content(self) -> ComposeResult:
        """Render the Logs view content.

        Yields:
            ComposeResult: The composed widgets
        """
        yield Label("Agent Logs", classes="logs-title")

        with Horizontal(classes="logs-toolbar"):
            yield Select(
                [("All Agents", "all"), ("dev-001", "dev-001"), ("pm-001", "pm-001")],
                value="all",
                id="agent-select",
                prompt="Agent",
            )
            yield Input(placeholder="Filter logs...", id="filter-input")
            yield Button("▶ Live", id="btn-pause", variant="success")
            yield Static("0 logs", classes="logs-status", id="log-count")

        with Vertical(classes="logs-container"):
            yield RichLog(id="log-stream", highlight=True, markup=True)

    async def on_mount(self) -> None:
        """Handle mount event."""
        await super().on_mount()
        await self._load_data()

    async def _load_data(self) -> None:
        """Load initial mock log data."""
        self._all_logs = self._generate_mock_logs()
        self._refresh_display()

    def _generate_mock_logs(self) -> list[LogEntry]:
        """Generate mock log entries.

        Returns:
            List of log entries
        """
        agents = ["dev-001", "pm-001", "dev-local", "scheduler"]
        messages = [
            ("DEBUG", "Memory usage: {}MB"),
            ("DEBUG", "LLM request: {} tokens"),
            ("INFO", "Task #{} completed successfully"),
            ("INFO", "Tool execution: {}"),
            ("INFO", "Task #{} started"),
            ("WARNING", "Retrying after error (attempt {})"),
            ("WARNING", "High memory usage: {}MB"),
            ("ERROR", "Task execution timeout after {}s"),
            ("ERROR", "Failed to connect to LLM API: {}"),
        ]

        logs = []
        now = datetime.now()
        for _i in range(100):
            ts = now - timedelta(seconds=random.randint(0, 3600))
            agent = random.choice(agents)
            level, template = random.choice(messages)
            msg = template.format(random.randint(1, 5000))
            logs.append(LogEntry(ts, agent, level, msg))

        logs.sort(key=lambda x: x.timestamp, reverse=True)
        return logs

    def _format_log_line(self, entry: LogEntry) -> str:
        """Format a single log entry as a styled string.

        Args:
            entry: Log entry to format

        Returns:
            Formatted log line with Rich markup
        """
        ts_str = entry.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        level_color = self._get_level_color(entry.level)
        return f"[{ts_str}] [{level_color}]{entry.level:8}[/{level_color}] [{entry.agent:12}] {entry.message}"

    def _get_level_color(self, level: str) -> str:
        """Get Rich markup color for log level.

        Args:
            level: Log level string

        Returns:
            Rich color name or hex
        """
        colors = {
            "DEBUG": "grey",
            "INFO": "green",
            "WARNING": "yellow",
            "ERROR": "red",
            "FATAL": "bold red",
        }
        return colors.get(level.upper(), "white")

    def _get_filtered_logs(self) -> list[LogEntry]:
        """Get filtered log entries.

        Returns:
            Filtered list of log entries
        """
        filtered = self._all_logs

        if self.current_agent != "all":
            filtered = [entry for entry in filtered if entry.agent == self.current_agent]

        if self.filter_text:
            ft = self.filter_text.lower()
            filtered = [
                entry
                for entry in filtered
                if ft in entry.message.lower() or ft in entry.level.lower()
            ]

        return filtered

    def _refresh_display(self) -> None:
        """Refresh the log display."""
        if self.is_paused:
            return

        log_widget = self.query_one("#log-stream", RichLog)
        log_widget.clear()

        filtered = self._get_filtered_logs()
        count_label = self.query_one("#log-count", Static)
        count_label.update(f"{len(filtered)} logs")

        for entry in filtered[:500]:
            line = self._format_log_line(entry)
            log_widget.write(line)

    def watch_is_paused(self, is_paused: bool) -> None:
        """Watch pause state changes.

        Args:
            is_paused: New pause state
        """
        btn = self.query_one("#btn-pause", Button)
        if is_paused:
            btn.label = "⏸ Paused"
            btn.variant = "warning"
        else:
            btn.label = "▶ Live"
            btn.variant = "success"

    def watch_current_agent(self, _agent: str) -> None:
        """Watch agent filter changes."""
        self._refresh_display()

    def watch_filter_text(self, _text: str) -> None:
        """Watch filter text changes."""
        self._refresh_display()

    def on_select_changed(self, event: Select.Changed) -> None:
        """Handle agent selection change.

        Args:
            event: Select change event
        """
        if event.select.id == "agent-select":
            self.current_agent = str(event.value) if event.value else "all"

    def on_input_changed(self, event: Input.Changed) -> None:
        """Handle filter input change.

        Args:
            event: Input change event
        """
        if event.input.id == "filter-input":
            self.filter_text = event.value

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button press events.

        Args:
            event: Button press event
        """
        if event.button.id == "btn-pause":
            self.is_paused = not self.is_paused

    def action_focus_filter(self) -> None:
        """Focus the filter input."""
        self.query_one("#filter-input", Input).focus()

    def action_jump_latest(self) -> None:
        """Jump to the latest log entry."""
        # RichLog auto-scrolls to bottom on new writes
        self._refresh_display()

    def action_toggle_pause(self) -> None:
        """Toggle pause state."""
        self.is_paused = not self.is_paused
