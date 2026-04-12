"""Operator TUI components package.

Provides UI components for the Operator TUI application.
"""

from tlon.app.tui.operator.components.inspector import InspectorPanel
from tlon.app.tui.operator.components.navigation import NavigationBar
from tlon.app.tui.operator.components.sidebar import MiniEventStream, Sidebar
from tlon.app.tui.operator.components.agent_tree import AgentTree
from tlon.app.tui.operator.components.agent_detail import AgentDetail

__all__ = [
    "NavigationBar",
    "Sidebar",
    "InspectorPanel",
    "MiniEventStream",
    "AgentTree",
    "AgentDetail",
]
