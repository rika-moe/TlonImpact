"""Tree view component for Tlon TUI."""

from typing import Any

from textual.reactive import reactive
from textual.widgets import Tree


class TreeView(Tree):
    """Enhanced tree view component for displaying hierarchical data."""

    DEFAULT_CSS = """
    TreeView {
        width: 100%;
        height: 100%;
        background: $surface-darken-1;
        border: solid $primary-darken-2;
        padding: 0 1;
    }
    TreeView > .tree--label {
        text-style: none;
    }
    TreeView > .tree--cursor {
        background: $primary-darken-2;
    }
    TreeView > .tree--highlight {
        text-style: bold;
    }
    """

    selected_key: reactive[str | None] = reactive(None)

    def __init__(self, label: str = "Root", name: str | None = None, id: str | None = None) -> None:
        """Initialize tree view.

        Args:
            label: Root label
            name: Component name
            id: Component ID
        """
        super().__init__(label, name=name, id=id)
        self._nodes: dict[str, Any] = {}

    def add_node(
        self, label: str, key: str, parent_key: str | None = None, data: Any = None, icon: str = ""
    ) -> Any:
        """Add a node to the tree.

        Args:
            label: Node label
            key: Unique node key
            parent_key: Parent node key (None for root level)
            data: Optional data associated with node
            icon: Optional icon prefix

        Returns:
            The created node
        """
        full_label = f"{icon} {label}" if icon else label

        if parent_key and parent_key in self._nodes:
            parent = self._nodes[parent_key]
            node = parent.add(full_label, data=data)
        else:
            node = self.root.add(full_label, data=data)

        self._nodes[key] = node
        return node

    def remove_node(self, key: str) -> bool:
        """Remove a node from the tree.

        Args:
            key: Node key to remove

        Returns:
            True if node was removed
        """
        if key in self._nodes:
            node = self._nodes[key]
            node.remove()
            del self._nodes[key]
            return True
        return False

    def select_node(self, key: str) -> bool:
        """Select a node by key.

        Args:
            key: Node key to select

        Returns:
            True if node was selected
        """
        if key in self._nodes:
            self.selected_key = key
            self.select(self._nodes[key])
            return True
        return False

    def get_selected_data(self) -> Any:
        """Get data of currently selected node.

        Returns:
            Node data or None
        """
        if self.selected_key and self.selected_key in self._nodes:
            return self._nodes[self.selected_key].data
        return None

    def update_node_icon(self, key: str, icon: str) -> bool:
        """Update node icon.

        Args:
            key: Node key
            icon: New icon

        Returns:
            True if icon was updated
        """
        if key in self._nodes:
            node = self._nodes[key]
            # Preserve label text, update icon
            current_label = str(node.label)
            # Remove existing icon if any
            parts = current_label.split(" ", 1)
            label_text = parts[1] if len(parts) > 1 else parts[0]
            node.label = f"{icon} {label_text}"
            return True
        return False

    def clear_nodes(self) -> None:
        """Clear all nodes from the tree."""
        self.root.remove_children()
        self._nodes.clear()
