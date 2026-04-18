"""Dropdown component for Tlon TUI."""

from collections.abc import Callable
from typing import Any

from textual.widgets import Select


class Dropdown(Select):
    """Enhanced dropdown component with search and filtering."""

    DEFAULT_CSS = """
    Dropdown {
        width: auto;
        height: auto;
    }
    Dropdown > SelectOverlay {
        background: $surface;
        border: solid $primary;
    }
    Dropdown > SelectCurrent {
        background: $surface-darken-1;
        border: solid $primary-darken-2;
    }
    """

    def __init__(
        self,
        options: list[tuple[str, Any]] | None = None,
        prompt: str = "Select...",
        allow_blank: bool = True,
        **kwargs,
    ) -> None:
        """Initialize dropdown.

        Args:
            options: List of (label, value) tuples
            prompt: Prompt text when nothing selected
            allow_blank: Allow empty selection
            **kwargs: Additional arguments
        """
        super().__init__(options=options or [], prompt=prompt, allow_blank=allow_blank, **kwargs)
        self._options = options or []
        self._on_change_handler: Callable | None = None

    def set_options(self, options: list[tuple[str, Any]]) -> None:
        """Update dropdown options.

        Args:
            options: New list of (label, value) tuples
        """
        self._options = options
        super().set_options(options)

    def on_change(self, handler: Callable[[Any], None]) -> None:
        """Register change handler.

        Args:
            handler: Callback function receiving selected value
        """
        self._on_change_handler = handler

    def watch_value(self, value: Any) -> None:
        """Watch for value changes and trigger handler."""
        super().watch_value(value)
        if self._on_change_handler:
            self._on_change_handler(value)

    def select_by_label(self, label: str) -> bool:
        """Select option by label text.

        Args:
            label: Label text to select

        Returns:
            True if option was found and selected
        """
        for opt_label, opt_value in self._options:
            if opt_label == label:
                self.value = opt_value
                return True
        return False
