"""Base view class for Operator TUI views.

Provides the foundation for all views in the Operator TUI application,
including state management and refresh mechanisms.
"""

from __future__ import annotations

import time
from abc import abstractmethod
from typing import TYPE_CHECKING, Any

from textual.containers import Container
from textual.reactive import reactive

if TYPE_CHECKING:
    from textual.app import ComposeResult


class BaseView(Container):
    """Base class for all Operator views.

    Provides common functionality for view state management,
    refresh mechanisms, and lifecycle hooks.
    """

    # Reactive state for view refresh tracking
    is_loading: reactive[bool] = reactive(False)
    last_refresh: reactive[float | None] = reactive(None)
    error_message: reactive[str | None] = reactive(None)

    def __init__(self, view_id: str, **kwargs: Any) -> None:
        """Initialize the base view.

        Args:
            view_id: Unique identifier for this view
            **kwargs: Additional arguments passed to Container
        """
        super().__init__(**kwargs)
        self.view_id = view_id
        self._view_state: dict[str, Any] = {}

    def compose(self) -> ComposeResult:
        """Compose the view layout.

        Subclasses should override this method to define their UI.

        Yields:
            ComposeResult: The composed widgets
        """
        yield from self.render_content()

    @abstractmethod
    def render_content(self) -> ComposeResult:
        """Render the view content.

        Subclasses must implement this method to render their specific content.

        Yields:
            ComposeResult: The composed widgets
        """
        yield from ()

    async def on_mount(self) -> None:
        """Handle view mount event.

        Called when the view is mounted to the DOM.
        Subclasses can override to perform initialization.
        """
        await self.refresh_view()

    def on_show(self) -> None:
        """Handle view show event.

        Called when the view becomes visible.
        Subclasses can override to resume operations.
        """
        pass

    def on_hide(self) -> None:
        """Handle view hide event.

        Called when the view is hidden.
        Subclasses can override to pause operations.
        """
        pass

    async def refresh_view(self) -> None:
        """Refresh the view content.

        Reloads data and updates the UI. Can be called manually
        or triggered by the global refresh shortcut.
        """
        self.is_loading = True
        self.error_message = None

        try:
            await self._load_data()
            self.last_refresh = time.time()
        except Exception as e:
            self.error_message = str(e)
        finally:
            self.is_loading = False

    async def _load_data(self) -> None:
        """Load data for the view.

        Subclasses should override this to implement data loading.
        """
        pass

    def get_state(self, key: str, default: Any = None) -> Any:
        """Get a value from the view state.

        Args:
            key: State key
            default: Default value if key not found

        Returns:
            The state value or default
        """
        return self._view_state.get(key, default)

    def set_state(self, key: str, value: Any) -> None:
        """Set a value in the view state.

        Args:
            key: State key
            value: State value
        """
        self._view_state[key] = value

    def clear_state(self) -> None:
        """Clear all view state."""
        self._view_state.clear()

    def watch_is_loading(self, is_loading: bool) -> None:
        """Watch for loading state changes.

        Args:
            is_loading: New loading state
        """
        # Subclasses can override to show/hide loading indicators
        pass

    def watch_error_message(self, error_message: str | None) -> None:
        """Watch for error message changes.

        Args:
            error_message: New error message or None
        """
        # Subclasses can override to display error UI
        pass
