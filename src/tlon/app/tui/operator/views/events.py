"""Events view for Operator TUI.

Displays the system event stream and filtering interface with:
- Event stream table (Time | Agent | Level | Type | Message)
- Filter panel (Level / Type / Agent)
- Event detail expansion
- Keyboard shortcuts
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import Button, Input, Label, Select, Static

from tlon.app.tui.common.components.data_table import DataTable
from tlon.app.tui.operator.views.base import BaseView

if TYPE_CHECKING:
    from textual.app import ComposeResult


@dataclass
class EventItem:
    """Represents a single system event."""

    time: str
    agent: str
    level: str
    type: str
    message: str
    context: dict[str, str] = field(default_factory=dict)
    stack_trace: str = ""


class EventDetailPanel(Vertical):
    """Panel showing detailed information for a selected event."""

    DEFAULT_CSS = """
    EventDetailPanel {
        height: auto;
        max-height: 40%;
        background: $surface-darken-1;
        border-top: solid $primary;
        padding: 1;
    }
    EventDetailPanel .detail-header {
        height: 1;
        text-style: bold;
        color: $text-accent;
        margin-bottom: 1;
    }
    EventDetailPanel .detail-row {
        height: auto;
        margin: 0;
    }
    EventDetailPanel .detail-label {
        width: 12;
        color: $text-muted;
    }
    EventDetailPanel .detail-value {
        width: 1fr;
    }
    EventDetailPanel .detail-message {
        height: auto;
        margin: 1 0;
        color: $text;
    }
    EventDetailPanel .detail-context {
        height: auto;
        margin: 1 0;
        color: $text-secondary;
    }
    EventDetailPanel .detail-stack {
        height: auto;
        margin: 1 0;
        color: $text-muted;
    }
    EventDetailPanel .detail-actions {
        height: auto;
        margin-top: 1;
        align: left middle;
    }
    EventDetailPanel .detail-actions Button {
        margin: 0 1 0 0;
    }
    """

    def __init__(self, **kwargs) -> None:
        """Initialize the detail panel."""
        super().__init__(**kwargs)
        self._event: EventItem | None = None

    def compose(self) -> ComposeResult:
        """Compose the detail panel."""
        yield Label("Event Details", classes="detail-header")
        yield Static(
            "Select an event to view details", id="detail-placeholder", classes="detail-message"
        )
        yield Static("", id="detail-content", classes="detail-message")
        yield Static("", id="detail-context", classes="detail-context")
        yield Static("", id="detail-stack", classes="detail-stack")
        with Horizontal(classes="detail-actions"):
            yield Button("View Full Trace", id="btn-trace", variant="primary")
            yield Button("Related Logs", id="btn-logs")
            yield Button("Jump to Agent", id="btn-agent")

    def update_event(self, event: EventItem | None) -> None:
        """Update the detail panel with event data.

        Args:
            event: The event to display, or None to clear
        """
        self._event = event
        placeholder = self.query_one("#detail-placeholder", Static)
        content = self.query_one("#detail-content", Static)
        context = self.query_one("#detail-context", Static)
        stack = self.query_one("#detail-stack", Static)

        if event is None:
            placeholder.display = True
            content.update("")
            context.update("")
            stack.update("")
            return

        placeholder.display = False
        content.update(
            f"Time:    {event.time}\n"
            f"Agent:   {event.agent}\n"
            f"Level:   {event.level}\n"
            f"Type:    {event.type}\n\n"
            f"Message: {event.message}"
        )

        if event.context:
            ctx_lines = ["Context:"] + [f"  {k}: {v}" for k, v in event.context.items()]
            context.update("\n".join(ctx_lines))
        else:
            context.update("")

        if event.stack_trace:
            stack.update(f"Stack Trace:\n{event.stack_trace}")
        else:
            stack.update("")


class FilterPanel(Horizontal):
    """Filter controls for the events view."""

    DEFAULT_CSS = """
    FilterPanel {
        height: 3;
        background: $surface-darken-1;
        border-bottom: solid $primary;
        padding: 0 1;
        align: center middle;
    }
    FilterPanel .filter-label {
        width: auto;
        color: $text-muted;
        margin: 0 1;
    }
    FilterPanel .filter-select {
        width: 16;
        margin: 0 1;
    }
    FilterPanel .filter-input {
        width: 30;
        margin: 0 1;
    }
    FilterPanel .filter-spacer {
        width: 1fr;
    }
    """

    def __init__(self, **kwargs) -> None:
        """Initialize the filter panel."""
        super().__init__(**kwargs)

    def compose(self) -> ComposeResult:
        """Compose the filter panel."""
        yield Label("Level:", classes="filter-label")
        yield Select(
            [
                ("All", "all"),
                ("DEBUG", "debug"),
                ("INFO", "info"),
                ("WARN", "warn"),
                ("ERROR", "error"),
                ("FATAL", "fatal"),
            ],
            value="all",
            id="filter-level",
            classes="filter-select",
        )
        yield Label("Type:", classes="filter-label")
        yield Select(
            [
                ("All", "all"),
                ("task", "task"),
                ("tool", "tool"),
                ("lifecycle", "lifecycle"),
                ("system", "system"),
            ],
            value="all",
            id="filter-type",
            classes="filter-select",
        )
        yield Label("Agent:", classes="filter-label")
        yield Select(
            [("All", "all"), ("dev-001", "dev-001"), ("pm-001", "pm-001"), ("system", "system")],
            value="all",
            id="filter-agent",
            classes="filter-select",
        )
        yield Static("", classes="filter-spacer")
        yield Input(placeholder="Search...", id="filter-search", classes="filter-input")


class EventsView(BaseView):
    """Events view - displays system event stream.

    Provides a complete interface for viewing and filtering events:
    - Event stream table with Time, Agent, Level, Type, Message columns
    - Filter panel for Level, Type, Agent, and text search
    - Event detail panel that expands when an event is selected
    - Keyboard shortcuts for common actions
    """

    DEFAULT_CSS = """
    EventsView {
        layout: vertical;
        padding: 0;
    }
    EventsView .events-header {
        height: 1;
        text-style: bold;
        color: $text-accent;
        padding: 0 1;
        content-align: center middle;
    }
    EventsView .events-table-container {
        height: 1fr;
        padding: 0;
    }
    EventsView .events-table {
        width: 100%;
        height: 100%;
        border: solid $primary-darken-2;
    }
    EventsView .events-table > .datatable--header {
        text-style: bold;
        background: $surface-darken-1;
    }
    EventsView .events-table > .datatable--row {
        height: 1;
    }
    EventsView .events-table > .datatable--row-hover {
        background: $primary-darken-3;
    }
    EventsView .events-table > .datatable--row-cursor {
        background: $primary-darken-2;
    }
    EventsView .events-detail {
        height: auto;
        max-height: 40%;
        background: $surface-darken-1;
        border-top: solid $primary;
        padding: 1;
    }
    EventsView .events-detail.hidden {
        display: none;
    }
    EventsView .help-text {
        color: $text-muted;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("f", "toggle_filter", "Filter", show=True),
        Binding("g", "goto_latest", "Latest", show=True),
        Binding("slash", "search", "Search", show=True),
        Binding("up", "navigate_up", "Up", show=False),
        Binding("down", "navigate_down", "Down", show=False),
        Binding("k", "navigate_up", "Up", show=False),
        Binding("j", "navigate_down", "Down", show=False),
    ]

    # Reactive state
    selected_event: reactive[EventItem | None] = reactive(None)
    filter_level: reactive[str] = reactive("all")
    filter_type: reactive[str] = reactive("all")
    filter_agent: reactive[str] = reactive("all")
    filter_search: reactive[str] = reactive("")
    show_detail: reactive[bool] = reactive(False)

    def __init__(self, **kwargs) -> None:
        """Initialize the Events view."""
        super().__init__(view_id="events", **kwargs)
        self._events: list[EventItem] = []
        self._filtered_events: list[EventItem] = []

    def render_content(self) -> ComposeResult:
        """Render the Events view content.

        Yields:
            ComposeResult: The composed widgets
        """
        yield Label("Events Stream", classes="events-header")
        yield FilterPanel(id="filter-panel")

        with Vertical(classes="events-table-container"):
            yield DataTable(id="events-table", classes="events-table")

        detail = EventDetailPanel(id="event-detail", classes="events-detail hidden")
        yield detail

    async def on_mount(self) -> None:
        """Handle mount event."""
        await super().on_mount()
        await self._load_data()

    async def _load_data(self) -> None:
        """Load events data with mock data."""
        table = self.query_one("#events-table", DataTable)
        table.add_column("Time", width=10)
        table.add_column("Agent", width=12)
        table.add_column("Level", width=8)
        table.add_column("Type", width=10)
        table.add_column("Message", width=40)
        self._events = self._generate_mock_events()
        self._apply_filters()

    def _generate_mock_events(self) -> list[EventItem]:
        """Generate mock event data.

        Returns:
            List of mock EventItem instances
        """
        return [
            EventItem(
                time="16:32:01",
                agent="dev-001",
                level="INFO",
                type="task",
                message="Task completed: ",
                context={"Task ID": "task-142", "Duration": "2.3s"},
            ),
            EventItem(
                time="16:31:58",
                agent="pm-001",
                level="DEBUG",
                type="tool",
                message="Analysis complete",
                context={"Tool": "analyze_code", "Tokens": "1.2k"},
            ),
            EventItem(
                time="16:31:45",
                agent="dev-001",
                level="INFO",
                type="tool",
                message="execute_bash: ls -la",
                context={"Command": "ls -la", "Exit Code": "0"},
            ),
            EventItem(
                time="16:31:30",
                agent="system",
                level="ERROR",
                type="system",
                message="pm-001 task timeout",
                context={"Task ID": "task-556", "Started": "16:31:00", "Expected": "< 10s"},
                stack_trace='  File "agent.py", line 234, in execute\n    result = await self.llm.complete(prompt)',
            ),
            EventItem(
                time="16:31:15",
                agent="dev-001",
                level="WARN",
                type="lifecycle",
                message="Retrying after error",
                context={"Attempt": "2/3", "Delay": "1s"},
            ),
            EventItem(
                time="16:30:55",
                agent="review-001",
                level="INFO",
                type="task",
                message="Code review started for PR ",
                context={"PR": "", "Author": "dev-001"},
            ),
            EventItem(
                time="16:30:42",
                agent="system",
                level="FATAL",
                type="system",
                message="Database connection lost",
                context={"Host": "db-01", "Port": "5432"},
                stack_trace='  File "db.py", line 45, in connect\n    raise ConnectionError("timeout")',
            ),
            EventItem(
                time="16:30:30",
                agent="dev-001",
                level="DEBUG",
                type="task",
                message="Memory usage: 245MB",
                context={"Heap": "180MB", "Stack": "65MB"},
            ),
            EventItem(
                time="16:30:15",
                agent="pm-001",
                level="INFO",
                type="lifecycle",
                message="Agent started successfully",
                context={"PID": "12345", "Version": "1.2.0"},
            ),
            EventItem(
                time="16:30:00",
                agent="dev-001",
                level="WARN",
                type="tool",
                message="Deprecated API usage: file_read_v1",
                context={"API": "file_read_v1", "Replacement": "file_read_v2"},
            ),
        ]

    def _apply_filters(self) -> None:
        """Apply current filters to the event list and refresh the table."""
        self._filtered_events = [
            e
            for e in self._events
            if (self.filter_level == "all" or e.level.lower() == self.filter_level.lower())
            and (self.filter_type == "all" or e.type.lower() == self.filter_type.lower())
            and (self.filter_agent == "all" or e.agent == self.filter_agent)
            and (not self.filter_search or self.filter_search.lower() in e.message.lower())
        ]
        self._refresh_table()

    def _refresh_table(self) -> None:
        """Refresh the DataTable with filtered events."""
        table = self.query_one("#events-table", DataTable)
        table.clear()
        for idx, event in enumerate(self._filtered_events):
            table.add_row(
                event.time,
                event.agent,
                event.level,
                event.type,
                event.message,
                key=f"event-{idx}",
            )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Handle row selection in the events table.

        Args:
            event: Row selected event
        """
        row_index = event.cursor_row
        if 0 <= row_index < len(self._filtered_events):
            self.selected_event = self._filtered_events[row_index]
            self.show_detail = True
            self._update_detail_panel()

    def _update_detail_panel(self) -> None:
        """Update the detail panel visibility and content."""
        detail = self.query_one("#event-detail", EventDetailPanel)
        if self.show_detail and self.selected_event is not None:
            detail.remove_class("hidden")
            detail.update_event(self.selected_event)
        else:
            detail.add_class("hidden")
            detail.update_event(None)

    def watch_show_detail(self, show: bool) -> None:
        """Watch for detail panel visibility changes.

        Args:
            show: Whether to show the detail panel
        """
        self._update_detail_panel()

    def watch_selected_event(self, event: EventItem | None) -> None:
        """Watch for selected event changes.

        Args:
            event: The newly selected event
        """
        self._update_detail_panel()

    def watch_filter_level(self, value: str) -> None:
        """Watch for level filter changes.

        Args:
            value: New filter level value
        """
        self._apply_filters()

    def watch_filter_type(self, value: str) -> None:
        """Watch for type filter changes.

        Args:
            value: New filter type value
        """
        self._apply_filters()

    def watch_filter_agent(self, value: str) -> None:
        """Watch for agent filter changes.

        Args:
            value: New filter agent value
        """
        self._apply_filters()

    def watch_filter_search(self, value: str) -> None:
        """Watch for search filter changes.

        Args:
            value: New search text
        """
        self._apply_filters()

    def on_select_changed(self, event: Select.Changed) -> None:
        """Handle filter dropdown changes."""
        if event.select.id == "filter-level":
            self.filter_level = str(event.value) if event.value else "all"
        elif event.select.id == "filter-type":
            self.filter_type = str(event.value) if event.value else "all"
        elif event.select.id == "filter-agent":
            self.filter_agent = str(event.value) if event.value else "all"

    def action_toggle_filter(self) -> None:
        """Toggle filter panel focus or cycle through filter options."""
        self.app.notify("Filter: Use Level/Type/Agent dropdowns", severity="information")

    def action_goto_latest(self) -> None:
        """Jump to the latest event in the table."""
        table = self.query_one("#events-table", DataTable)
        if self._filtered_events:
            table.move_cursor(row=len(self._filtered_events) - 1)
            self.selected_event = self._filtered_events[-1]
            self.show_detail = True

    def action_search(self) -> None:
        """Focus the search input."""
        search_input = self.query_one("#filter-search", Input)
        search_input.focus()

    def action_navigate_up(self) -> None:
        """Navigate up in the events table."""
        table = self.query_one("#events-table", DataTable)
        table.action_cursor_up()
        self._sync_selection_from_cursor()

    def action_navigate_down(self) -> None:
        """Navigate down in the events table."""
        table = self.query_one("#events-table", DataTable)
        table.action_cursor_down()
        self._sync_selection_from_cursor()

    def _sync_selection_from_cursor(self) -> None:
        """Sync selected_event with the current cursor position."""
        table = self.query_one("#events-table", DataTable)
        cursor_row = table.cursor_row
        if cursor_row is not None and 0 <= cursor_row < len(self._filtered_events):
            self.selected_event = self._filtered_events[cursor_row]
            self.show_detail = True
