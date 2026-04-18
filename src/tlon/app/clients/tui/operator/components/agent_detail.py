"""Agent detail component for Operator TUI.

Provides the right panel with detailed agent information and actions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Button, Label, Static

from tlon.app.clients.tui.common.styles.colors import get_status_icon
from tlon.app.clients.tui.common.utils.time_utils import format_duration
from tlon.app.clients.tui.operator.components.agent_tree import AgentInfo

if TYPE_CHECKING:
    from textual.app import ComposeResult


class AgentDetail(Vertical):
    """Agent detail panel component.

    Displays detailed information about a selected agent including:
    - Basic info (Role, Model, Status)
    - Runtime statistics (Uptime, Tasks, Errors)
    - Resource usage (CPU, Memory)
    - Connection status (WebSocket)
    - Action buttons (Restart, Stop, View Logs, Config)
    """

    DEFAULT_CSS = """
    AgentDetail {
        width: 100%;
        height: 100%;
        background: $surface-darken-1;
        padding: 1;
    }
    AgentDetail .detail-header {
        height: 1;
        text-style: bold;
        color: $text-accent;
        margin-bottom: 1;
    }
    AgentDetail .detail-content {
        height: 1fr;
        overflow: auto;
    }
    AgentDetail .detail-section {
        margin: 1 0;
    }
    AgentDetail .detail-label {
        color: $text-muted;
    }
    AgentDetail .detail-value {
        text-style: bold;
    }
    AgentDetail .status-healthy { color: $success; }
    AgentDetail .status-idle { color: $text-muted; }
    AgentDetail .status-busy { color: $warning; }
    AgentDetail .status-degraded { color: $error; }
    AgentDetail .status-error { color: $error; }
    AgentDetail .status-paused { color: $primary; }
    AgentDetail .detail-actions {
        height: auto;
        margin-top: 2;
    }
    AgentDetail .action-btn {
        margin: 0 1 1 0;
    }
    AgentDetail .no-selection {
        color: $text-muted;
        text-style: italic;
    }
    AgentDetail .metric-row {
        height: 1;
        margin: 0;
    }
    AgentDetail .separator {
        height: 1;
        background: $primary-darken-2;
        margin: 1 0;
    }
    """

    agent: reactive[AgentInfo | None] = reactive(None)

    def __init__(self, **kwargs) -> None:
        """Initialize the agent detail panel."""
        super().__init__(**kwargs)

    def compose(self) -> ComposeResult:
        """Compose the agent detail panel.

        Yields:
            ComposeResult: The composed widgets
        """
        yield Label("Inspector", classes="detail-header")
        yield Static(
            self._render_no_selection(), classes="detail-content", id="detail-content", markup=True
        )

        with Horizontal(classes="detail-actions"):
            yield Button("Restart", id="btn-restart", classes="action-btn", disabled=True)
            yield Button("Stop", id="btn-stop", classes="action-btn", disabled=True)
            yield Button("View Logs", id="btn-logs", classes="action-btn", disabled=True)
            yield Button("Config", id="btn-config", classes="action-btn", disabled=True)

    def _render_no_selection(self) -> str:
        """Render content when no agent is selected.

        Returns:
            Content string
        """
        return "[No agent selected]\n\nSelect an agent from the tree to view details."

    def _render_agent(self, agent: AgentInfo) -> str:
        """Render agent details.

        Args:
            agent: Agent information

        Returns:
            Content string
        """
        status_icon = get_status_icon(agent.status)
        ws_status = "● connected" if agent.ws_connected else "○ disconnected"
        ws_color_class = "status-healthy" if agent.ws_connected else "status-error"

        error_rate = (agent.errors / agent.tasks * 100) if agent.tasks > 0 else 0.0

        lines = [
            f"{agent.name}",
            "────────────────────",
            "",
            f"Role: {agent.role.capitalize()}",
            f"Model: {agent.model}",
            f"Status: {status_icon} {agent.status}",
            "",
            f"Uptime: {format_duration(agent.uptime)}",
            f"Tasks: {agent.tasks} total",
            f"Errors: {agent.errors} ({error_rate:.2f}%)",
            "",
            f"CPU: {agent.cpu:.0f}%  MEM: {agent.memory:.0f}MB",
            f"WS: [{ws_color_class}]{ws_status}[/{ws_color_class}]",
        ]

        return "\n".join(lines)

    def watch_agent(self, agent: AgentInfo | None) -> None:
        """Watch for agent changes and update UI.

        Args:
            agent: New agent or None
        """
        content = self.query_one("#detail-content", Static)
        restart_btn = self.query_one("#btn-restart", Button)
        stop_btn = self.query_one("#btn-stop", Button)
        logs_btn = self.query_one("#btn-logs", Button)
        config_btn = self.query_one("#btn-config", Button)

        if agent:
            content.update(self._render_agent(agent))
            restart_btn.disabled = False
            stop_btn.disabled = False
            logs_btn.disabled = False
            config_btn.disabled = False

            # Update stop button label based on status
            if agent.status == "paused":
                stop_btn.label = "Start"
            else:
                stop_btn.label = "Stop"
        else:
            content.update(self._render_no_selection())
            restart_btn.disabled = True
            stop_btn.disabled = True
            logs_btn.disabled = True
            config_btn.disabled = True

    def update_agent(self, agent: AgentInfo | None) -> None:
        """Update the displayed agent.

        Args:
            agent: Agent to display or None
        """
        self.agent = agent

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button press.

        Args:
            event: Button press event
        """
        if not self.agent:
            return

        button_id = event.button.id
        if button_id == "btn-restart":
            self.post_message(self.AgentAction(self.agent.id, "restart"))
        elif button_id == "btn-stop":
            action = "start" if self.agent.status == "paused" else "stop"
            self.post_message(self.AgentAction(self.agent.id, action))
        elif button_id == "btn-logs":
            self.post_message(self.AgentAction(self.agent.id, "logs"))
        elif button_id == "btn-config":
            self.post_message(self.AgentAction(self.agent.id, "config"))

    class AgentAction(Message):
        """Message sent when an agent action is triggered."""

        def __init__(self, agent_id: str, action: str) -> None:
            """Initialize message.

            Args:
                agent_id: Target agent ID
                action: Action to perform (restart, stop, start, logs, config)
            """
            super().__init__()
            self.agent_id = agent_id
            self.action = action
