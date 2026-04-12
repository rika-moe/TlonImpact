"""State management utilities for Tlon TUI."""

from dataclasses import dataclass, field
from typing import Any, Callable, Generic, TypeVar
from textual.reactive import reactive
from textual.message import Message

T = TypeVar("T")


class StateChange(Message, Generic[T]):
    """Message sent when state changes."""

    def __init__(self, key: str, old_value: T, new_value: T) -> None:
        """Initialize state change message.

        Args:
            key: State key that changed
            old_value: Previous value
            new_value: New value
        """
        super().__init__()
        self.key = key
        self.old_value = old_value
        self.new_value = new_value


class StateManager:
    """Reactive state manager for TUI components."""

    def __init__(self) -> None:
        """Initialize state manager."""
        self._state: dict[str, Any] = {}
        self._listeners: dict[str, list[Callable[[Any, Any], None]]] = {}

    def get(self, key: str, default: Any = None) -> Any:
        """Get state value.

        Args:
            key: State key
            default: Default value if key not found

        Returns:
            State value or default
        """
        return self._state.get(key, default)

    def set(self, key: str, value: Any) -> bool:
        """Set state value and notify listeners.

        Args:
            key: State key
            value: New value

        Returns:
            True if value changed
        """
        old_value = self._state.get(key)
        if old_value != value:
            self._state[key] = value
            self._notify(key, old_value, value)
            return True
        return False

    def update(self, updates: dict[str, Any]) -> list[str]:
        """Update multiple state values.

        Args:
            updates: Dictionary of key-value pairs

        Returns:
            List of keys that changed
        """
        changed = []
        for key, value in updates.items():
            if self.set(key, value):
                changed.append(key)
        return changed

    def subscribe(self, key: str, callback: Callable[[Any, Any], None]) -> Callable:
        """Subscribe to state changes.

        Args:
            key: State key to watch
            callback: Function(old_value, new_value) to call on change

        Returns:
            Unsubscribe function
        """
        if key not in self._listeners:
            self._listeners[key] = []
        self._listeners[key].append(callback)

        def unsubscribe() -> None:
            """Unsubscribe from state changes."""
            if key in self._listeners and callback in self._listeners[key]:
                self._listeners[key].remove(callback)

        return unsubscribe

    def _notify(self, key: str, old_value: Any, new_value: Any) -> None:
        """Notify listeners of state change.

        Args:
            key: State key that changed
            old_value: Previous value
            new_value: New value
        """
        if key in self._listeners:
            for callback in self._listeners[key]:
                try:
                    callback(old_value, new_value)
                except Exception as e:
                    # Log error but don't break other listeners
                    print(f"State listener error for {key}: {e}")

    def clear(self) -> None:
        """Clear all state."""
        self._state.clear()
        self._listeners.clear()

    def keys(self) -> list[str]:
        """Get all state keys.

        Returns:
            List of state keys
        """
        return list(self._state.keys())

    def to_dict(self) -> dict[str, Any]:
        """Export state as dictionary.

        Returns:
            Copy of state dictionary
        """
        return dict(self._state)


@dataclass
class CongressState:
    """State for Congress TUI."""

    current_agent: str | None = None
    messages: list[dict] = field(default_factory=list)
    input_history: list[str] = field(default_factory=list)
    is_typing: bool = False
    agents: dict[str, dict] = field(default_factory=dict)
    connection_state: str = "disconnected"


@dataclass
class OperatorState:
    """State for Operator TUI."""

    current_view: str = "agents"
    selected_agent: str | None = None
    agents: dict[str, dict] = field(default_factory=dict)
    events: list[dict] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
    traces: list[dict] = field(default_factory=list)
    filters: dict[str, Any] = field(default_factory=dict)
    connection_state: str = "disconnected"
