"""Data table component for Tlon TUI."""

from textual.widgets import DataTable as TextualDataTable
from textual.reactive import reactive
from typing import Any


class DataTable(TextualDataTable):
    """Enhanced data table component with filtering and sorting."""

    DEFAULT_CSS = """
    DataTable {
        width: 100%;
        height: 100%;
        border: solid $primary-darken-2;
    }
    DataTable > .datatable--header {
        text-style: bold;
        background: $surface-darken-1;
    }
    DataTable > .datatable--row {
        height: 1;
    }
    DataTable > .datatable--row-hover {
        background: $primary-darken-3;
    }
    DataTable > .datatable--row-cursor {
        background: $primary-darken-2;
    }
    """

    filter_text: reactive[str] = reactive("")

    def __init__(
        self,
        columns: list[str] | None = None,
        column_widths: dict[str, int] | None = None,
        **kwargs
    ) -> None:
        """Initialize data table.

        Args:
            columns: List of column names
            column_widths: Dictionary mapping column names to widths
            **kwargs: Additional arguments passed to DataTable
        """
        super().__init__(**kwargs)
        self._columns = columns or []
        self._column_widths = column_widths or {}
        self._all_rows: list[tuple] = []
        self._filtered_rows: list[tuple] = []

    def compose(self):
        """Compose the data table."""
        # Add columns
        for col in self._columns:
            width = self._column_widths.get(col)
            self.add_column(col, width=width)

    def add_row_data(self, *cells: Any, key: str | None = None) -> None:
        """Add a row to the table.

        Args:
            *cells: Cell values
            key: Optional row key
        """
        self._all_rows.append((cells, key))
        if self._matches_filter(cells):
            self.add_row(*cells, key=key)

    def _matches_filter(self, cells: tuple) -> bool:
        """Check if row matches current filter.

        Args:
            cells: Row cell values

        Returns:
            True if row matches filter
        """
        if not self.filter_text:
            return True
        filter_lower = self.filter_text.lower()
        return any(
            filter_lower in str(cell).lower()
            for cell in cells
        )

    def set_filter(self, text: str) -> None:
        """Set filter text and refresh table.

        Args:
            text: Filter text
        """
        self.filter_text = text
        self._apply_filter()

    def _apply_filter(self) -> None:
        """Apply current filter to all rows."""
        self.clear()
        for cells, key in self._all_rows:
            if self._matches_filter(cells):
                self.add_row(*cells, key=key)

    def clear_all(self) -> None:
        """Clear all rows including stored data."""
        self._all_rows.clear()
        self.clear()

    def sort_by_column(self, column: str, reverse: bool = False) -> None:
        """Sort table by column.

        Args:
            column: Column name to sort by
            reverse: Sort in descending order
        """
        if column not in self._columns:
            return

        col_index = self._columns.index(column)
        self._all_rows.sort(
            key=lambda x: x[0][col_index] if col_index < len(x[0]) else "",
            reverse=reverse
        )
        self._apply_filter()
