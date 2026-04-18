"""CommandPalette - Quick command palette.

Triggered by / key, shows available commands:
- /clear - Clear conversation
- /agents - View agent list
- /help - Show help
- /quit - Exit program
"""

from __future__ import annotations

from dataclasses import dataclass

from textual.containers import Vertical
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Label, ListItem, ListView


@dataclass
class Command:
    """Command definition."""

    name: str
    description: str
    shortcut: str = ""


class CommandPalette(Vertical):
    """Quick command palette.

    Shows available slash commands with descriptions.
    """

    DEFAULT_CSS = """
    CommandPalette {
        width: 40;
        height: auto;
        max-height: 15;
        background: $surface;
        border: solid $primary;
        display: none;
        position: absolute;
        layer: popup;
        offset: 0 -100%;
    }

    CommandPalette.open {
        display: block;
    }

    CommandPalette #palette-title {
        text-style: bold;
        background: $primary-darken-2;
        color: $text;
        padding: 0 1;
        height: 1;
    }

    CommandPalette #command-list {
        width: 100%;
        height: auto;
        border: none;
        background: transparent;
    }

    CommandPalette .list-item {
        padding: 0 1;
        color: $text;
        height: 2;
    }

    CommandPalette .list-item:hover {
        background: $primary-darken-2;
    }

    CommandPalette .command-name {
        text-style: bold;
        color: $primary-lighten-2;
    }

    CommandPalette .command-desc {
        color: $text-muted;
        text-style: dim;
    }
    """

    class Selected(Message):
        """Message sent when a command is selected."""

        def __init__(self, command: str) -> None:
            """Initialize message.

            Args:
                command: Selected command string (e.g., "/clear")
            """
            super().__init__()
            self.command = command

    is_open: reactive[bool] = reactive(False)

    COMMANDS = [
        Command("clear", "清空对话", "/clear"),
        Command("agents", "查看 Agent 列表", "/agents"),
        Command("help", "显示帮助", "/help"),
        Command("quit", "退出程序", "/quit"),
    ]

    def __init__(self, *args, **kwargs):
        """Initialize command palette."""
        super().__init__(*args, **kwargs)
        self._item_map: dict[int, str] = {}

    def compose(self):
        """Compose command palette."""
        yield Label("快捷命令:", id="palette-title")
        yield ListView(id="command-list")

    def open(self) -> None:
        """Open the command palette."""
        self._refresh_list()
        self.is_open = True
        self.add_class("open")

    def close(self) -> None:
        """Close the command palette."""
        self.is_open = False
        self.remove_class("open")

    def _refresh_list(self) -> None:
        """Refresh the command list."""
        list_view = self.query_one("#command-list", ListView)
        list_view.clear()
        self._item_map.clear()

        for i, cmd in enumerate(self.COMMANDS):
            item = ListItem(
                Label(f"/{cmd.name} - {cmd.description}"),
                classes="list-item",
            )
            list_view.append(item)
            self._item_map[i] = cmd.shortcut

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Handle list item selection."""
        list_view = self.query_one("#command-list", ListView)
        try:
            index = list_view.children.index(event.item)
        except ValueError:
            return
        command = self._item_map.get(index, "")

        self.post_message(self.Selected(command))
        self.close()

    def on_key(self, event) -> None:
        """Handle key events."""
        if not self.is_open:
            return

        if event.key == "escape":
            self.close()
            event.stop()
        elif event.key == "enter":
            list_view = self.query_one("#command-list", ListView)
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
                        command = self._item_map.get(index, "")
                        self.post_message(self.Selected(command))
                        self.close()
            event.stop()
