"""Tlon TUI - Terminal User Interface components.

This package provides TUI components for both Congress (chat) and Operator (admin) interfaces.
"""

from tlon.app.tui.common.components import (
    DataTable,
    Dropdown,
    Header,
    Modal,
    StatusBar,
    Toast,
    TreeView,
)
from tlon.app.tui.common.styles.colors import (
    AgentStatusColors,
    LogLevelColors,
    ThemeColors,
)
from tlon.app.tui.common.utils.state import StateManager
from tlon.app.tui.common.utils.time_utils import format_duration, format_timestamp
from tlon.app.tui.operator.app import OperatorApp

__all__ = [
    # Common Components
    "Header",
    "StatusBar",
    "DataTable",
    "TreeView",
    "Dropdown",
    "Modal",
    "Toast",
    # Colors
    "ThemeColors",
    "AgentStatusColors",
    "LogLevelColors",
    # Utils
    "format_timestamp",
    "format_duration",
    "StateManager",
    # Operator
    "OperatorApp",
]
