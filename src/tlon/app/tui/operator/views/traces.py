"""Traces view for Operator TUI.

Displays distributed traces and performance metrics with:
- Trace list table (ID / Root / Start / Duration / Status)
- Text-based flame chart for selected trace
- Status filtering (completed / partial / failed)
- Detail panel with full stage breakdown
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from textual.binding import Binding
from textual.containers import Vertical
from textual.reactive import reactive
from textual.widgets import Input, Label, Static

from tlon.app.tui.common.components.data_table import DataTable
from tlon.app.tui.operator.views.base import BaseView

if TYPE_CHECKING:
    from textual.app import ComposeResult


@dataclass
class TraceSpan:
    """A single span within a trace."""

    name: str
    duration: float
    children: list[TraceSpan] = field(default_factory=list)
    parallel: bool = False


@dataclass
class TraceInfo:
    """Information about a distributed trace."""

    trace_id: str
    root_agent: str
    start_time: str
    duration: float
    status: str
    spans: list[TraceSpan] = field(default_factory=list)


class TracesView(BaseView):
    """Traces view - displays distributed traces with flame chart.

    Provides a complete interface for browsing and inspecting traces:
    - Trace list with filtering and sorting
    - Text-based flame chart visualization
    - Detail panel with span breakdown
    """

    DEFAULT_CSS = """
    TracesView {
        layout: horizontal;
        padding: 0;
    }
    TracesView .traces-sidebar {
        width: 40%;
        height: 100%;
        background: $surface-darken-1;
        border-right: solid $primary;
        layout: vertical;
    }
    TracesView .traces-header {
        height: 1;
        text-style: bold;
        color: $text-accent;
        padding: 1 1 0 1;
    }
    TracesView .traces-toolbar {
        height: auto;
        padding: 0 1;
        color: $text-muted;
    }
    TracesView .traces-table-container {
        height: 1fr;
        padding: 0 1 1 1;
    }
    TracesView .traces-detail {
        width: 60%;
        height: 100%;
        background: $surface;
        layout: vertical;
        padding: 1;
    }
    TracesView .detail-header {
        height: 1;
        text-style: bold;
        color: $text-accent;
        margin-bottom: 1;
    }
    TracesView .detail-content {
        height: 1fr;
        border: solid $primary-darken-2;
        padding: 1;
        overflow-y: auto;
    }
    TracesView .flame-chart {
        width: 100%;
        height: auto;
    }
    TracesView .flame-row {
        height: 1;
        margin: 0;
    }
    TracesView .flame-bar {
        color: $success;
    }
    TracesView .flame-bar-partial {
        color: $warning;
    }
    TracesView .flame-bar-failed {
        color: $error;
    }
    TracesView .status-completed {
        color: $success;
    }
    TracesView .status-partial {
        color: $warning;
    }
    TracesView .status-failed {
        color: $error;
    }
    TracesView .search-input {
        display: none;
        height: auto;
        margin: 0 1 1 1;
    }
    TracesView .search-input.visible {
        display: block;
    }
    TracesView .help-text {
        color: $text-muted;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("/", "search", "Search", show=True),
        Binding("f", "filter_status", "Filter Status", show=True),
        Binding("up", "navigate_up", "Up", show=False),
        Binding("down", "navigate_down", "Down", show=False),
        Binding("k", "navigate_up", "Up", show=False),
        Binding("j", "navigate_down", "Down", show=False),
        Binding("escape", "cancel_search", "Cancel", show=False),
    ]

    _filter_status: reactive[str] = reactive("all")
    _search_mode: reactive[bool] = reactive(False)

    def __init__(self, **kwargs) -> None:
        """Initialize the Traces view."""
        super().__init__(view_id="traces", **kwargs)
        self._traces: list[TraceInfo] = []
        self._selected_trace: TraceInfo | None = None

    def render_content(self) -> ComposeResult:
        """Render the Traces view content.

        Yields:
            ComposeResult: The composed widgets
        """
        # Left sidebar - Trace list
        with Vertical(classes="traces-sidebar"):
            yield Label("Traces", classes="traces-header")
            yield Static(
                "Shortcuts: / search  f filter  ↑/↓ navigate",
                classes="traces-toolbar",
            )
            yield Input(
                placeholder="Search traces...",
                classes="search-input",
                id="trace-search",
            )
            with Vertical(classes="traces-table-container"):
                yield DataTable(
                    columns=["Trace ID", "Root", "Start", "Duration", "Status"],
                    column_widths={
                        "Trace ID": 12,
                        "Root": 10,
                        "Start": 8,
                        "Duration": 10,
                        "Status": 12,
                    },
                    id="trace-table",
                )

        # Right detail panel
        with Vertical(classes="traces-detail"):
            yield Label("Trace Detail", classes="detail-header", id="detail-header")
            with Vertical(classes="detail-content"):
                yield Static(
                    "Select a trace to view details and flame chart.",
                    id="detail-content",
                )

    def on_mount(self) -> None:
        """Handle mount event."""
        super().on_mount()
        self.call_after_refresh(self._load_data)

    async def _load_data(self) -> None:
        """Load mock trace data."""
        self._traces = [
            TraceInfo(
                trace_id="trace-001",
                root_agent="dev-001",
                start_time="16:32",
                duration=2.3,
                status="completed",
                spans=[
                    TraceSpan(
                        name="dev-001",
                        duration=2.3,
                        children=[
                            TraceSpan(name="llm", duration=1.2),
                            TraceSpan(
                                name="tool",
                                duration=0.5,
                                children=[
                                    TraceSpan(name="file_read", duration=0.3),
                                ],
                            ),
                            TraceSpan(name="parse", duration=0.4),
                        ],
                    ),
                    TraceSpan(
                        name="pm-001",
                        duration=0.8,
                        parallel=True,
                        children=[
                            TraceSpan(name="review", duration=0.8),
                        ],
                    ),
                ],
            ),
            TraceInfo(
                trace_id="trace-002",
                root_agent="pm-001",
                start_time="16:30",
                duration=15.2,
                status="partial",
                spans=[
                    TraceSpan(
                        name="pm-001",
                        duration=15.2,
                        children=[
                            TraceSpan(name="planning", duration=3.5),
                            TraceSpan(name="llm", duration=8.2),
                            TraceSpan(
                                name="tool",
                                duration=2.1,
                                children=[
                                    TraceSpan(name="search", duration=1.2),
                                    TraceSpan(name="file_read", duration=0.6),
                                ],
                            ),
                            TraceSpan(name="parse", duration=1.0),
                        ],
                    ),
                ],
            ),
            TraceInfo(
                trace_id="trace-003",
                root_agent="dev-001",
                start_time="16:28",
                duration=0.8,
                status="failed",
                spans=[
                    TraceSpan(
                        name="dev-001",
                        duration=0.8,
                        children=[
                            TraceSpan(name="llm", duration=0.5),
                            TraceSpan(name="tool", duration=0.2),
                            TraceSpan(name="error_handler", duration=0.1),
                        ],
                    ),
                ],
            ),
            TraceInfo(
                trace_id="trace-004",
                root_agent="scheduler",
                start_time="16:25",
                duration=5.6,
                status="completed",
                spans=[
                    TraceSpan(
                        name="scheduler",
                        duration=5.6,
                        children=[
                            TraceSpan(name="dispatch", duration=0.8),
                            TraceSpan(
                                name="dev-001",
                                duration=3.2,
                                parallel=True,
                                children=[
                                    TraceSpan(name="llm", duration=2.0),
                                    TraceSpan(name="tool", duration=0.8),
                                ],
                            ),
                            TraceSpan(
                                name="dev-002",
                                duration=2.8,
                                parallel=True,
                                children=[
                                    TraceSpan(name="llm", duration=1.8),
                                    TraceSpan(name="parse", duration=0.6),
                                ],
                            ),
                            TraceSpan(name="collect", duration=0.5),
                        ],
                    ),
                ],
            ),
            TraceInfo(
                trace_id="trace-005",
                root_agent="dev-002",
                start_time="16:20",
                duration=3.1,
                status="completed",
                spans=[
                    TraceSpan(
                        name="dev-002",
                        duration=3.1,
                        children=[
                            TraceSpan(name="llm", duration=1.5),
                            TraceSpan(
                                name="tool",
                                duration=1.0,
                                children=[
                                    TraceSpan(name="execute_bash", duration=0.7),
                                    TraceSpan(name="file_write", duration=0.2),
                                ],
                            ),
                            TraceSpan(name="parse", duration=0.4),
                        ],
                    ),
                ],
            ),
        ]
        self._refresh_table()

    def _refresh_table(self) -> None:
        """Refresh the trace table with current data."""
        table = self.query_one("#trace-table", DataTable)
        table.clear_all()

        for trace in self._traces:
            if self._filter_status != "all" and trace.status != self._filter_status:
                continue

            status_display = {
                "completed": "[completed]✅ completed[/]",
                "partial": "[partial]⚠️ partial[/]",
                "failed": "[failed]❌ failed[/]",
            }.get(trace.status, trace.status)

            table.add_row_data(
                trace.trace_id,
                trace.root_agent,
                trace.start_time,
                f"{trace.duration:.1f}s",
                status_display,
                key=trace.trace_id,
            )

    def _render_flame_chart(self, trace: TraceInfo) -> str:
        """Render a text-based flame chart for a trace.

        Args:
            trace: The trace to render

        Returns:
            Text representation of the flame chart
        """
        lines: list[str] = []
        max_bar_width = 48
        total_duration = max(trace.duration, 0.001)

        def render_span(span: TraceSpan, depth: int = 0, prefix: str = "") -> None:
            indent = "  " * depth
            bar_len = int((span.duration / total_duration) * max_bar_width)
            bar = "█" * max(bar_len, 1)
            parallel_label = " (parallel)" if span.parallel else ""
            line = f"{indent}{prefix}{span.name}  {bar} {span.duration:.1f}s{parallel_label}"
            lines.append(line)

            for i, child in enumerate(span.children):
                is_last = i == len(span.children) - 1
                child_prefix = "└─ " if is_last else "├─ "
                render_span(child, depth + 1, child_prefix)

        for span in trace.spans:
            render_span(span)
            lines.append("")

        return "\n".join(lines)

    def _render_detail(self, trace: TraceInfo) -> str:
        """Render detailed trace information.

        Args:
            trace: The trace to render

        Returns:
            Text representation of trace details
        """
        status_icon = {
            "completed": "✅",
            "partial": "⚠️",
            "failed": "❌",
        }.get(trace.status, "❓")

        lines = [
            f"Trace: {trace.trace_id}  ({trace.duration:.1f}s)",
            "",
            f"  Root Agent:  {trace.root_agent}",
            f"  Start Time:  {trace.start_time}",
            f"  Status:      {status_icon} {trace.status}",
            "",
            "  Flame Chart:",
            "  " + "─" * 56,
            self._render_flame_chart(trace),
            "  " + "─" * 56,
            "",
            "  Stage Breakdown:",
        ]

        def collect_spans(span: TraceSpan, depth: int = 0) -> None:
            indent = "    " + "  " * depth
            pct = (span.duration / trace.duration) * 100 if trace.duration > 0 else 0
            lines.append(f"{indent}{span.name}: {span.duration:.2f}s ({pct:.1f}%)")
            for child in span.children:
                collect_spans(child, depth + 1)

        for span in trace.spans:
            collect_spans(span)

        return "\n".join(lines)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Handle trace selection from table.

        Args:
            event: Row selected event
        """
        trace_id = event.row_key.value if event.row_key else None
        if not trace_id:
            return

        trace = next((t for t in self._traces if t.trace_id == trace_id), None)
        if trace:
            self._selected_trace = trace
            self._update_detail(trace)

    def _update_detail(self, trace: TraceInfo) -> None:
        """Update the detail panel with trace information.

        Args:
            trace: The selected trace
        """
        header = self.query_one("#detail-header", Label)
        header.update(f"Trace: {trace.trace_id} ({trace.duration:.1f}s)")

        content = self.query_one("#detail-content", Static)
        content.update(self._render_detail(trace))

    def action_search(self) -> None:
        """Activate search mode."""
        self._search_mode = True
        search_input = self.query_one("#trace-search", Input)
        search_input.add_class("visible")
        search_input.focus()

    def action_cancel_search(self) -> None:
        """Cancel search mode."""
        self._search_mode = False
        search_input = self.query_one("#trace-search", Input)
        search_input.remove_class("visible")
        search_input.value = ""
        table = self.query_one("#trace-table", DataTable)
        table.set_filter("")

    def on_input_changed(self, event: Input.Changed) -> None:
        """Handle search input changes.

        Args:
            event: Input changed event
        """
        if event.input.id == "trace-search":
            table = self.query_one("#trace-table", DataTable)
            table.set_filter(event.value)

    def action_filter_status(self) -> None:
        """Cycle through status filters."""
        filters = ["all", "completed", "partial", "failed"]
        current_index = filters.index(self._filter_status)
        next_index = (current_index + 1) % len(filters)
        self._filter_status = filters[next_index]

        status_label = {
            "all": "All",
            "completed": "Completed",
            "partial": "Partial",
            "failed": "Failed",
        }.get(self._filter_status, self._filter_status)

        self.app.notify(f"Filter: {status_label}", severity="information")
        self._refresh_table()

    def watch__filter_status(self, status: str) -> None:
        """Watch for filter status changes.

        Args:
            status: New filter status
        """
        self._refresh_table()

    def action_navigate_up(self) -> None:
        """Navigate up in the trace table."""
        table = self.query_one("#trace-table", DataTable)
        table.action_cursor_up()

    def action_navigate_down(self) -> None:
        """Navigate down in the trace table."""
        table = self.query_one("#trace-table", DataTable)
        table.action_cursor_down()
