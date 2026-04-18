"""AgentSelector - Agent selection dropdown component.

Click the robot icon to expand dropdown list showing:
- Agent status (online/idle/busy/offline)
- @All option to notify everyone
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Button, Label, ListItem, ListView


@dataclass
class AgentInfo:
    """Agent information for display."""

    id: str
    name: str
    state: str = "unknown"
    emoji: str = "🤖"

    @property
    def status_icon(self) -> str:
        """Get status icon based on state."""
        icons = {
            "online": "🟢",
            "idle": "🟡",
            "busy": "🔴",
            "offline": "⚪",
            "unknown": "⚪",
        }
        return icons.get(self.state, "⚪")

    @property
    def display_name(self) -> str:
        """Get display name with status."""
        return f"{self.status_icon} @{self.name}"


class AgentSelector(Horizontal):
    """Agent selector with dropdown.

    Shows current agent and allows switching via dropdown.
    """

    DEFAULT_CSS = """
    AgentSelector {
        width: auto;
        height: auto;
        align: center middle;
    }

    AgentSelector #selector-button {
        width: auto;
        min-width: 6;
        content-align: center middle;
    }

    AgentSelector #dropdown {
        width: 30;
        height: auto;
        max-height: 15;
        background: $surface;
        border: solid $primary;
        display: none;
        position: absolute;
        layer: popup;
    }

    AgentSelector #dropdown.open {
        display: block;
    }

    AgentSelector #agent-list {
        width: 100%;
        height: auto;
        border: none;
        background: transparent;
    }

    AgentSelector .list-item {
        padding: 0 1;
        color: $text;
    }

    AgentSelector .list-item:hover {
        background: $primary-darken-2;
    }

    AgentSelector .all-option {
        text-style: bold;
        border-top: solid $primary-darken-2;
        margin-top: 1;
        padding-top: 1;
    }
    """

    class Changed(Message):
        """Message sent when agent selection changes."""

        def __init__(self, agent_id: str | None) -> None:
            """Initialize message.

            Args:
                agent_id: Selected agent ID or None for @All
            """
            super().__init__()
            self.agent_id = agent_id

    agents: reactive[list[AgentInfo]] = reactive([])
    current_agent_id: reactive[str | None] = reactive(None)
    is_open: reactive[bool] = reactive(False)

    def __init__(self, *args, **kwargs):
        """Initialize agent selector."""
        super().__init__(*args, **kwargs)
        self._agent_map: dict[str, AgentInfo] = {}
        self._item_map: dict[int, str | None] = {}

    def compose(self):
        """Compose agent selector."""
        yield Button("🤖 ▼", id="selector-button")

        with Vertical(id="dropdown"):
            yield ListView(id="agent-list")

    def update_agents(self, agents: list[Any]) -> None:
        """Update the agent list.

        Args:
            agents: List of agent info objects
        """
        self._agent_map = {}
        agent_list = []

        for agent in agents:
            if isinstance(agent, dict):
                info = AgentInfo(
                    id=agent.get("id", "unknown"),
                    name=agent.get("name", "Unknown"),
                    state=agent.get("state", "unknown"),
                    emoji=agent.get("emoji", "🤖"),
                )
            else:
                info = agent

            self._agent_map[info.id] = info
            agent_list.append(info)

        # Sort: online/idle first, then busy, then offline
        state_order = {"online": 0, "idle": 1, "busy": 2, "offline": 3, "unknown": 4}
        agent_list.sort(key=lambda a: state_order.get(a.state, 4))

        self.agents = agent_list
        self._refresh_list()

    def _refresh_list(self) -> None:
        """Refresh the dropdown list."""
        list_view = self.query_one("#agent-list", ListView)
        list_view.clear()
        self._item_map.clear()

        # Add agents
        for i, agent in enumerate(self.agents):
            item = ListItem(
                Label(agent.display_name),
                classes="list-item",
            )
            list_view.append(item)
            self._item_map[i] = agent.id

        # Add @All option
        all_item = ListItem(
            Label("@All - 通知所有人"),
            classes="list-item all-option",
        )
        list_view.append(all_item)
        self._item_map[len(self.agents)] = None

    def select_agent(self, agent_id: str | None) -> None:
        """Select an agent.

        Args:
            agent_id: Agent ID to select or None for @All
        """
        self.current_agent_id = agent_id

        # Update button label
        button = self.query_one("#selector-button", Button)
        if agent_id and agent_id in self._agent_map:
            agent = self._agent_map[agent_id]
            button.label = f"{agent.emoji} ▼"
        else:
            button.label = "🤖 ▼"

        # Close dropdown
        self._close_dropdown()

        # Post message
        self.post_message(self.Changed(agent_id))

    def _toggle_dropdown(self) -> None:
        """Toggle dropdown visibility."""
        self.is_open = not self.is_open
        dropdown = self.query_one("#dropdown", Vertical)

        if self.is_open:
            dropdown.add_class("open")
        else:
            dropdown.remove_class("open")

    def _close_dropdown(self) -> None:
        """Close dropdown."""
        self.is_open = False
        dropdown = self.query_one("#dropdown", Vertical)
        dropdown.remove_class("open")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button press."""
        if event.button.id == "selector-button":
            self._toggle_dropdown()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Handle list item selection."""
        list_view = self.query_one("#agent-list", ListView)
        try:
            index = list_view.children.index(event.item)
        except ValueError:
            return
        agent_id = self._item_map.get(index)
        self.select_agent(agent_id)

    def on_click(self, event) -> None:
        """Handle click - close dropdown if clicking outside."""
        if not self.is_open:
            return

        # Check if click is inside dropdown
        dropdown = self.query_one("#dropdown", Vertical)
        if dropdown.screen_region.contains(event.screen_x, event.screen_y):
            return

        self._close_dropdown()
