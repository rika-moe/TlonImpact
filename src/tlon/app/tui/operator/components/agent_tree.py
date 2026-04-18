"""Agent tree component for Operator TUI.

Provides the left sidebar tree view for displaying agents organized by groups.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from textual.containers import Vertical
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Button, Label
from textual.widgets._tree import TreeNode

from tlon.app.tui.common.components.tree_view import TreeView
from tlon.app.tui.common.styles.colors import get_status_icon

if TYPE_CHECKING:
    from textual.app import ComposeResult


@dataclass
class AgentInfo:
    """Agent information data class."""

    id: str
    name: str
    role: str = "default"
    model: str = "unknown"
    status: str = "offline"
    group: str = "default"
    uptime: float = 0.0
    tasks: int = 0
    errors: int = 0
    cpu: float = 0.0
    memory: float = 0.0
    ws_connected: bool = False
    data: dict[str, Any] = field(default_factory=dict)


class AgentTree(Vertical):
    """Agent tree component with folder grouping.

    Displays agents organized in groups (production, development, system)
    with status icons and keyboard navigation.
    """

    DEFAULT_CSS = """
    AgentTree {
        width: 100%;
        height: 1fr;
        background: $surface-darken-1;
        padding: 0;
    }
    AgentTree .tree-header {
        height: 1;
        text-style: bold;
        color: $text-accent;
        margin: 1 0;
        padding: 0 1;
    }
    AgentTree .tree-container {
        height: 1fr;
        overflow: auto;
        padding: 0 1;
    }
    AgentTree .tree-footer {
        height: 3;
        padding: 1;
    }
    AgentTree .new-agent-btn {
        width: 100%;
    }
    AgentTree .folder-icon {
        color: $text-accent;
    }
    AgentTree .agent-icon {
        color: $text;
    }
    AgentTree .status-icon {
        color: $text-muted;
    }
    AgentTree .status-healthy { color: $success; }
    AgentTree .status-idle { color: $text-muted; }
    AgentTree .status-busy { color: $warning; }
    AgentTree .status-degraded { color: $error; }
    AgentTree .status-error { color: $error; }
    AgentTree .status-paused { color: $primary; }
    """

    selected_agent: reactive[str | None] = reactive(None)

    def __init__(self, **kwargs) -> None:
        """Initialize the agent tree."""
        super().__init__(**kwargs)
        self._agents: dict[str, AgentInfo] = {}
        self._groups: dict[str, TreeNode] = {}
        self._agent_nodes: dict[str, TreeNode] = {}

    def compose(self) -> ComposeResult:
        """Compose the agent tree.

        Yields:
            ComposeResult: The composed widgets
        """
        yield Label("Agents", classes="tree-header")

        with Vertical(classes="tree-container"):
            tree = TreeView(label="Root", id="agent-tree-view")
            tree.root.expand()
            yield tree

        with Vertical(classes="tree-footer"):
            yield Button("+ New Agent", id="new-agent-btn", classes="new-agent-btn")

    def on_mount(self) -> None:
        """Handle mount event."""
        # Agent data is loaded externally via add_agent() calls
        pass

    def add_agent(self, agent: AgentInfo) -> None:
        """Add an agent to the tree.

        Args:
            agent: Agent information
        """
        self._agents[agent.id] = agent

        # Ensure group exists
        if agent.group not in self._groups:
            self._add_group(agent.group)

        # Add agent node under group
        group_node = self._groups[agent.group]

        status_icon = get_status_icon(agent.status)

        label = f"{agent.name} {status_icon}"
        node = group_node.add(label, data=agent)
        self._agent_nodes[agent.id] = node

    def _add_group(self, group_name: str) -> None:
        """Add a folder group to the tree.

        Args:
            group_name: Name of the group
        """
        tree = self.query_one("#agent-tree-view", TreeView)
        folder_icon = "📁"
        node = tree.root.add(
            f"{folder_icon} {group_name}", data={"type": "group", "name": group_name}
        )
        node.expand()
        self._groups[group_name] = node

    def remove_agent(self, agent_id: str) -> bool:
        """Remove an agent from the tree.

        Args:
            agent_id: Agent ID to remove

        Returns:
            True if agent was removed
        """
        if agent_id in self._agent_nodes:
            node = self._agent_nodes[agent_id]
            node.remove()
            del self._agent_nodes[agent_id]
            del self._agents[agent_id]
            return True
        return False

    def update_agent_status(self, agent_id: str, status: str) -> bool:
        """Update agent status icon.

        Args:
            agent_id: Agent ID
            status: New status

        Returns:
            True if updated
        """
        if agent_id in self._agents:
            self._agents[agent_id].status = status

        if agent_id in self._agent_nodes:
            node = self._agent_nodes[agent_id]
            agent = self._agents[agent_id]
            status_icon = get_status_icon(status)
            node.label = f"{agent.name} {status_icon}"
            return True
        return False

    def select_agent(self, agent_id: str | None) -> bool:
        """Select an agent by ID.

        Args:
            agent_id: Agent ID to select

        Returns:
            True if agent was selected
        """
        if agent_id is None:
            self.selected_agent = None
            return True

        if agent_id in self._agent_nodes:
            self.selected_agent = agent_id
            tree = self.query_one("#agent-tree-view", TreeView)
            tree.select(self._agent_nodes[agent_id])
            return True
        return False

    def get_selected_agent(self) -> AgentInfo | None:
        """Get currently selected agent info.

        Returns:
            Selected agent info or None
        """
        if self.selected_agent and self.selected_agent in self._agents:
            return self._agents[self.selected_agent]
        return None

    def get_agent(self, agent_id: str) -> AgentInfo | None:
        """Get agent info by ID.

        Args:
            agent_id: Agent ID

        Returns:
            Agent info or None
        """
        return self._agents.get(agent_id)

    def on_tree_node_selected(self, event) -> None:
        """Handle tree node selection.

        Args:
            event: Tree node selected event
        """
        node = event.node
        if node and node.data and isinstance(node.data, AgentInfo):
            self.selected_agent = node.data.id
            self.post_message(self.AgentSelected(node.data.id, node.data))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button press.

        Args:
            event: Button press event
        """
        if event.button.id == "new-agent-btn":
            self.post_message(self.NewAgentRequested())

    def action_new_agent(self) -> None:
        """Action to create new agent."""
        self.post_message(self.NewAgentRequested())

    class AgentSelected(Message):
        """Message sent when an agent is selected."""

        def __init__(self, agent_id: str, agent_info: AgentInfo) -> None:
            """Initialize message.

            Args:
                agent_id: Selected agent ID
                agent_info: Selected agent information
            """
            super().__init__()
            self.agent_id = agent_id
            self.agent_info = agent_info

    class NewAgentRequested(Message):
        """Message sent when new agent is requested."""

        def __init__(self) -> None:
            """Initialize message."""
            super().__init__()
