"""Agents view for Operator TUI.

Displays the agent tree and management interface with:
- Left sidebar: Agent tree with folder grouping
- Main content: Agent list with details
- Right panel: Agent detail inspector
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static

from tlon.app.tui.common.components.modal import ConfirmModal
from tlon.app.tui.operator.components.agent_detail import AgentDetail
from tlon.app.tui.operator.components.agent_tree import AgentInfo, AgentTree
from tlon.app.tui.operator.views.base import BaseView

if TYPE_CHECKING:
    from textual.app import ComposeResult


class NewAgentModal(ModalScreen):
    """Modal for creating a new agent."""

    DEFAULT_CSS = """
    NewAgentModal {
        align: center middle;
    }
    NewAgentModal > Vertical {
        background: $surface;
        border: solid $primary;
        padding: 1 2;
        width: 50;
        height: auto;
    }
    NewAgentModal .modal-title {
        text-style: bold;
        color: $text-accent;
        margin-bottom: 1;
    }
    NewAgentModal .input-label {
        margin-top: 1;
    }
    NewAgentModal .buttons {
        height: auto;
        margin-top: 2;
        align: center middle;
    }
    NewAgentModal .buttons Button {
        margin: 0 1;
    }
    """

    def compose(self) -> ComposeResult:
        """Compose the modal."""
        with Vertical():
            yield Label("Create New Agent", classes="modal-title")
            yield Label("Name:", classes="input-label")
            yield Input(placeholder="agent-name", id="agent-name")
            yield Label("Role:", classes="input-label")
            yield Input(placeholder="developer", id="agent-role")
            yield Label("Model:", classes="input-label")
            yield Input(placeholder="GPT-4", id="agent-model")
            yield Label("Group:", classes="input-label")
            yield Input(placeholder="production", id="agent-group")

            with Horizontal(classes="buttons"):
                yield Button("Create", id="btn-create", variant="primary")
                yield Button("Cancel", id="btn-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button press."""
        if event.button.id == "btn-create":
            name = self.query_one("#agent-name", Input).value
            role = self.query_one("#agent-role", Input).value or "developer"
            model = self.query_one("#agent-model", Input).value or "GPT-4"
            group = self.query_one("#agent-group", Input).value or "production"

            if name:
                self.dismiss(
                    {
                        "name": name,
                        "role": role,
                        "model": model,
                        "group": group,
                    }
                )
            else:
                self.app.notify("Agent name is required", severity="error")
        else:
            self.dismiss(None)


class EditAgentModal(ModalScreen):
    """Modal for editing an agent."""

    DEFAULT_CSS = """
    EditAgentModal {
        align: center middle;
    }
    EditAgentModal > Vertical {
        background: $surface;
        border: solid $primary;
        padding: 1 2;
        width: 50;
        height: auto;
    }
    EditAgentModal .modal-title {
        text-style: bold;
        color: $text-accent;
        margin-bottom: 1;
    }
    EditAgentModal .input-label {
        margin-top: 1;
    }
    EditAgentModal .buttons {
        height: auto;
        margin-top: 2;
        align: center middle;
    }
    EditAgentModal .buttons Button {
        margin: 0 1;
    }
    """

    def __init__(self, agent: AgentInfo, **kwargs) -> None:
        """Initialize with agent to edit."""
        super().__init__(**kwargs)
        self.agent = agent

    def compose(self) -> ComposeResult:
        """Compose the modal."""
        with Vertical():
            yield Label(f"Edit Agent: {self.agent.name}", classes="modal-title")
            yield Label("Role:", classes="input-label")
            yield Input(value=self.agent.role, id="agent-role")
            yield Label("Model:", classes="input-label")
            yield Input(value=self.agent.model, id="agent-model")
            yield Label("Group:", classes="input-label")
            yield Input(value=self.agent.group, id="agent-group")

            with Horizontal(classes="buttons"):
                yield Button("Save", id="btn-save", variant="primary")
                yield Button("Cancel", id="btn-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button press."""
        if event.button.id == "btn-save":
            role = self.query_one("#agent-role", Input).value
            model = self.query_one("#agent-model", Input).value
            group = self.query_one("#agent-group", Input).value

            self.dismiss(
                {
                    "role": role,
                    "model": model,
                    "group": group,
                }
            )
        else:
            self.dismiss(None)


class AgentsView(BaseView):
    """Agents view - displays agent tree and management.

    Provides a complete interface for managing agents:
    - Tree view with folder grouping
    - Agent detail panel
    - Keyboard shortcuts for common actions
    """

    DEFAULT_CSS = """
    AgentsView {
        layout: horizontal;
        padding: 0;
    }
    AgentsView .agents-sidebar {
        width: 25%;
        height: 100%;
        background: $surface-darken-1;
        border-right: solid $primary;
    }
    AgentsView .agents-main {
        width: 50%;
        height: 100%;
        background: $surface;
        padding: 1;
    }
    AgentsView .agents-inspector {
        width: 25%;
        height: 100%;
        background: $surface-darken-1;
        border-left: solid $primary;
    }
    AgentsView .main-header {
        height: 1;
        text-style: bold;
        color: $text-accent;
        margin-bottom: 1;
    }
    AgentsView .main-content {
        height: 1fr;
    }
    AgentsView .help-text {
        color: $text-muted;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("n", "new_agent", "New Agent", show=True),
        Binding("e", "edit_agent", "Edit", show=True),
        Binding("d", "delete_agent", "Delete", show=True),
        Binding("s", "toggle_agent", "Stop/Start", show=True),
        Binding("l", "view_logs", "View Logs", show=True),
        Binding("space", "toggle_expand", "Expand/Collapse", show=True),
        Binding("up", "navigate_up", "Up", show=False),
        Binding("down", "navigate_down", "Down", show=False),
        Binding("k", "navigate_up", "Up", show=False),
        Binding("j", "navigate_down", "Down", show=False),
    ]

    def __init__(self, **kwargs) -> None:
        """Initialize the Agents view."""
        super().__init__(view_id="agents", **kwargs)
        self._agents: dict[str, AgentInfo] = {}

    def render_content(self) -> ComposeResult:
        """Render the Agents view content.

        Yields:
            ComposeResult: The composed widgets
        """
        # Left sidebar - Agent Tree
        with Vertical(classes="agents-sidebar"):
            yield AgentTree(id="agent-tree")

        # Main content area
        with Vertical(classes="agents-main"):
            yield Label("Agents Management", classes="main-header")
            yield Static(
                "Select an agent from the tree to view and manage it.\n\n"
                "Shortcuts:\n"
                "  n - New Agent\n"
                "  e - Edit Agent\n"
                "  d - Delete Agent\n"
                "  s - Stop/Start Agent\n"
                "  l - View Logs\n"
                "  Space - Expand/Collapse Group\n"
                "  ↑/↓ or j/k - Navigate",
                classes="main-content",
            )

        # Right inspector panel
        with Vertical(classes="agents-inspector"):
            yield AgentDetail(id="agent-detail")

    def on_mount(self) -> None:
        """Handle mount event."""
        super().on_mount()
        # Load initial data
        self.call_after_refresh(self._load_data)

    async def _load_data(self) -> None:
        """Load agent data."""
        # In a real implementation, this would fetch from the server
        pass

    def on_agent_tree_agent_selected(self, event: AgentTree.AgentSelected) -> None:
        """Handle agent selection from tree.

        Args:
            event: Agent selected event
        """
        detail = self.query_one("#agent-detail", AgentDetail)
        detail.update_agent(event.agent_info)

    def on_agent_tree_new_agent_requested(self, event: AgentTree.NewAgentRequested) -> None:
        """Handle new agent request.

        Args:
            event: New agent requested event
        """
        self.action_new_agent()

    def on_agent_detail_agent_action(self, event: AgentDetail.AgentAction) -> None:
        """Handle agent action from detail panel.

        Args:
            event: Agent action event
        """
        if event.action == "restart":
            self._restart_agent(event.agent_id)
        elif event.action in ("stop", "start"):
            self._toggle_agent(event.agent_id)
        elif event.action == "logs":
            self._view_logs(event.agent_id)
        elif event.action == "config":
            self._edit_config(event.agent_id)

    def action_new_agent(self) -> None:
        """Action to create a new agent."""

        def handle_result(result: dict | None) -> None:
            if result:
                tree = self.query_one("#agent-tree", AgentTree)
                agent = AgentInfo(
                    id=result["name"],
                    name=result["name"],
                    role=result["role"],
                    model=result["model"],
                    status="idle",
                    group=result["group"],
                )
                tree.add_agent(agent)
                self.app.notify(f"Agent '{result['name']}' created", severity="information")

        self.app.push_screen(NewAgentModal(), handle_result)

    def action_edit_agent(self) -> None:
        """Action to edit the selected agent."""
        tree = self.query_one("#agent-tree", AgentTree)
        agent = tree.get_selected_agent()

        if not agent:
            self.app.notify("No agent selected", severity="warning")
            return

        def handle_result(result: dict | None) -> None:
            if result:
                # Update agent info
                agent.role = result["role"]
                agent.model = result["model"]
                agent.group = result["group"]

                # Refresh UI
                detail = self.query_one("#agent-detail", AgentDetail)
                detail.update_agent(agent)
                self.app.notify(f"Agent '{agent.name}' updated", severity="information")

        self.app.push_screen(EditAgentModal(agent), handle_result)

    def action_delete_agent(self) -> None:
        """Action to delete the selected agent."""
        tree = self.query_one("#agent-tree", AgentTree)
        agent = tree.get_selected_agent()

        if not agent:
            self.app.notify("No agent selected", severity="warning")
            return

        def handle_confirm(result: str) -> None:
            if result == "yes":
                tree.remove_agent(agent.id)
                detail = self.query_one("#agent-detail", AgentDetail)
                detail.update_agent(None)
                self.app.notify(f"Agent '{agent.name}' deleted", severity="information")

        self.app.push_screen(
            ConfirmModal(
                title="Delete Agent", message=f"Are you sure you want to delete '{agent.name}'?"
            ),
            handle_confirm,
        )

    def action_toggle_agent(self) -> None:
        """Action to stop/start the selected agent."""
        tree = self.query_one("#agent-tree", AgentTree)
        agent = tree.get_selected_agent()

        if not agent:
            self.app.notify("No agent selected", severity="warning")
            return

        self._toggle_agent(agent.id)

    def _toggle_agent(self, agent_id: str) -> None:
        """Toggle agent stop/start.

        Args:
            agent_id: Agent ID to toggle
        """
        tree = self.query_one("#agent-tree", AgentTree)
        agent = tree.get_agent(agent_id)

        if not agent:
            return

        if agent.status == "paused":
            agent.status = "healthy"
            tree.update_agent_status(agent_id, "healthy")
            self.app.notify(f"Agent '{agent.name}' started", severity="information")
        else:
            agent.status = "paused"
            tree.update_agent_status(agent_id, "paused")
            self.app.notify(f"Agent '{agent.name}' stopped", severity="information")

        # Update detail panel
        detail = self.query_one("#agent-detail", AgentDetail)
        if detail.agent and detail.agent.id == agent_id:
            detail.update_agent(agent)

    def _restart_agent(self, agent_id: str) -> None:
        """Restart an agent.

        Args:
            agent_id: Agent ID to restart
        """
        tree = self.query_one("#agent-tree", AgentTree)
        agent = tree.get_agent(agent_id)

        if not agent:
            return

        # Simulate restart
        agent.status = "busy"
        tree.update_agent_status(agent_id, "busy")
        self.app.notify(f"Agent '{agent.name}' restarting...", severity="information")

        # Simulate restart completion
        def complete_restart() -> None:
            agent.status = "healthy"
            agent.uptime = 0
            tree.update_agent_status(agent_id, "healthy")
            detail = self.query_one("#agent-detail", AgentDetail)
            if detail.agent and detail.agent.id == agent_id:
                detail.update_agent(agent)
            self.app.notify(f"Agent '{agent.name}' restarted", severity="information")

        self.set_timer(2.0, complete_restart)

    def action_view_logs(self) -> None:
        """Action to view logs for the selected agent."""
        tree = self.query_one("#agent-tree", AgentTree)
        agent = tree.get_selected_agent()

        if not agent:
            self.app.notify("No agent selected", severity="warning")
            return

        self._view_logs(agent.id)

    def _view_logs(self, agent_id: str) -> None:
        """View logs for an agent.

        Args:
            agent_id: Agent ID to view logs for
        """
        tree = self.query_one("#agent-tree", AgentTree)
        agent = tree.get_agent(agent_id)

        if agent:
            # Switch to logs view
            if hasattr(self.app, "action_switch_view"):
                self.app.action_switch_view("logs")
            self.app.notify(f"Viewing logs for '{agent.name}'", severity="information")

    def _edit_config(self, agent_id: str) -> None:
        """Edit configuration for an agent.

        Args:
            agent_id: Agent ID to edit config for
        """
        tree = self.query_one("#agent-tree", AgentTree)
        agent = tree.get_agent(agent_id)

        if agent:
            # Switch to config view
            if hasattr(self.app, "action_switch_view"):
                self.app.action_switch_view("config")
            self.app.notify(f"Editing config for '{agent.name}'", severity="information")

    def action_toggle_expand(self) -> None:
        """Toggle expand/collapse of the selected tree node."""
        # This would be handled by the tree component
        pass

    def action_navigate_up(self) -> None:
        """Navigate up in the tree."""
        # This would be handled by the tree component
        pass

    def action_navigate_down(self) -> None:
        """Navigate down in the tree."""
        # This would be handled by the tree component
        pass
