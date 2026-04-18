"""MentionPopup - @Mention autocomplete popup.

Triggered when typing @, shows:
- Agent list sorted by status (idle agents first)
- Fuzzy search support
- Arrow key navigation, Enter to select
"""

from __future__ import annotations

from typing import Any

from textual.containers import Vertical
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Label, ListItem, ListView

from ..state import AgentInfo, AgentState


class MentionPopup(Vertical):
    """@Mention autocomplete popup.

    Shows when user types @ and provides agent name completion.
    """

    DEFAULT_CSS = """
    MentionPopup {
        width: 30;
        height: auto;
        max-height: 12;
        background: $surface;
        border: solid $primary;
        display: none;
        position: absolute;
        layer: popup;
        offset: 0 1;
    }

    MentionPopup.open {
        display: block;
    }

    MentionPopup #popup-title {
        text-style: bold;
        background: $primary-darken-2;
        color: $text;
        padding: 0 1;
        height: 1;
    }

    MentionPopup #agent-list {
        width: 100%;
        height: auto;
        border: none;
        background: transparent;
    }

    MentionPopup .list-item {
        padding: 0 1;
        color: $text;
    }

    MentionPopup .list-item:hover {
        background: $primary-darken-2;
    }

    MentionPopup .list-item.selected {
        background: $primary;
        color: $text;
    }

    MentionPopup .status-online {
        color: $success;
    }

    MentionPopup .status-idle {
        color: $warning;
    }

    MentionPopup .status-busy {
        color: $error;
    }

    MentionPopup .status-offline {
        color: $text-muted;
        text-style: dim;
    }
    """

    class Selected(Message):
        """Message sent when an agent is selected."""

        def __init__(self, agent_id: str | None, agent_name: str) -> None:
            """Initialize message.

            Args:
                agent_id: Selected agent ID or None for @All
                agent_name: Display name of the agent
            """
            super().__init__()
            self.agent_id = agent_id
            self.agent_name = agent_name

    is_open: reactive[bool] = reactive(False)
    query: reactive[str] = reactive("")

    def __init__(self, *args, **kwargs):
        """Initialize mention popup."""
        super().__init__(*args, **kwargs)
        self._all_agents: list[AgentInfo] = []
        self._filtered_agents: list[AgentInfo] = []
        self._item_map: dict[int, tuple[str | None, str]] = {}

    def compose(self):
        """Compose mention popup."""
        yield Label("选择 Agent:", id="popup-title")
        yield ListView(id="agent-list")

    def open(self, query: str = "", agents: list[Any] | None = None) -> None:
        """Open the mention popup.

        Args:
            query: Current query text after @
            agents: List of available agents
        """
        self.query = query

        if agents is not None:
            self._all_agents = self._normalize_agents(agents)

        self._filter_agents()
        self._refresh_list()

        self.is_open = True
        self.add_class("open")

        # Position popup near input
        self.styles.offset = (
            0,
            -self._filtered_agents.__len__() - 3 if self._filtered_agents else -5,
        )

    def close(self) -> None:
        """Close the mention popup."""
        self.is_open = False
        self.remove_class("open")

    def _normalize_agents(self, agents: list[Any]) -> list[AgentInfo]:
        """Normalize agent data to AgentInfo objects.

        Args:
            agents: List of agent data (dicts or AgentInfo)

        Returns:
            List of AgentInfo objects
        """
        result = []
        for agent in agents:
            info = AgentInfo.from_dict(agent) if isinstance(agent, dict) else agent
            result.append(info)
        return result

    def _filter_agents(self) -> None:
        """Filter agents based on query."""
        query = self.query.lower()

        if not query:
            self._filtered_agents = list(self._all_agents)
        else:
            self._filtered_agents = [
                a for a in self._all_agents if query in a.name.lower() or query in a.id.lower()
            ]

        # Sort by status: idle first, then online, then busy, then offline
        state_order = {
            AgentState.IDLE: 0,
            AgentState.ACTIVE: 1,
            AgentState.BUSY: 2,
            AgentState.OFFLINE: 3,
            AgentState.THINKING: 4,
        }
        self._filtered_agents.sort(key=lambda a: state_order.get(a.state, 5))

    def _refresh_list(self) -> None:
        """Refresh the agent list."""
        list_view = self.query_one("#agent-list", ListView)
        list_view.clear()
        self._item_map.clear()

        # Add @All option first
        all_item = ListItem(
            Label("@All - 通知所有人"),
            classes="list-item",
        )
        list_view.append(all_item)
        self._item_map[0] = (None, "All")

        # Add filtered agents
        for i, agent in enumerate(self._filtered_agents, start=1):
            status_class = f"status-{agent.state.value}"
            display = f"{agent.emoji} @{agent.name}"

            item = ListItem(
                Label(display, classes=status_class),
                classes="list-item",
            )
            list_view.append(item)
            self._item_map[i] = (agent.id, agent.name)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Handle list item selection."""
        list_view = self.query_one("#agent-list", ListView)
        try:
            index = list_view.children.index(event.item)
        except ValueError:
            return
        agent_id, agent_name = self._item_map.get(index, (None, ""))

        self.post_message(self.Selected(agent_id, agent_name))
        self.close()

    def on_key(self, event) -> None:
        """Handle key events for navigation."""
        if not self.is_open:
            return

        list_view = self.query_one("#agent-list", ListView)

        if event.key == "escape":
            self.close()
            event.stop()
        elif event.key == "enter":
            # Select current item
            if list_view.index is not None:
                try:
                    item = list_view.children[list_view.index]
                except IndexError:
                    item = None
                if item:
                    try:
                        index = list_view.children.index(item)
                    except ValueError:
                        index = None
                    if index is not None:
                        agent_id, agent_name = self._item_map.get(index, (None, ""))
                        self.post_message(self.Selected(agent_id, agent_name))
                        self.close()
            event.stop()
