"""Modal dialog component for Tlon TUI."""

from textual.widgets import Static, Button
from textual.containers import Vertical, Horizontal
from textual.screen import ModalScreen
from typing import Callable


class Modal(ModalScreen):
    """Modal dialog component."""

    DEFAULT_CSS = """
    Modal {
        align: center middle;
    }
    Modal > Vertical {
        background: $surface;
        border: solid $primary;
        padding: 1 2;
        width: 60;
        height: auto;
    }
    Modal > Vertical > Static {
        width: 100%;
        content-align: center middle;
        text-style: bold;
        margin: 1 0;
    }
    Modal > Vertical > Horizontal {
        height: auto;
        content-align: center middle;
        margin-top: 1;
    }
    Modal > Vertical > Horizontal > Button {
        margin: 0 1;
    }
    """

    def __init__(
        self,
        title: str,
        message: str,
        buttons: list[tuple[str, str]] | None = None,
        **kwargs
    ) -> None:
        """Initialize modal dialog.

        Args:
            title: Dialog title
            message: Dialog message
            buttons: List of (label, result) tuples, default is [("OK", "ok")]
            **kwargs: Additional arguments
        """
        super().__init__(**kwargs)
        self.title = title
        self.message = message
        self.buttons = buttons or [("OK", "ok")]
        self._result_handler: Callable[[str], None] | None = None

    def compose(self):
        """Compose the modal dialog."""
        with Vertical():
            yield Static(self.title)
            yield Static(self.message)
            with Horizontal():
                for label, result in self.buttons:
                    btn = Button(label, id=f"btn_{result}")
                    btn.data_result = result
                    yield btn

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button press."""
        button = event.button
        result = getattr(button, "data_result", "ok")
        self.dismiss(result)

    def on_dismiss(self, result: str) -> None:
        """Handle dialog dismissal."""
        if self._result_handler:
            self._result_handler(result)

    def on_result(self, handler: Callable[[str], None]) -> None:
        """Register result handler.

        Args:
            handler: Callback function receiving button result
        """
        self._result_handler = handler


class ConfirmModal(Modal):
    """Confirmation modal with Yes/No buttons."""

    def __init__(self, title: str = "Confirm", message: str = "Are you sure?", **kwargs) -> None:
        """Initialize confirmation modal.

        Args:
            title: Dialog title
            message: Confirmation message
            **kwargs: Additional arguments
        """
        super().__init__(
            title=title,
            message=message,
            buttons=[("Yes", "yes"), ("No", "no")],
            **kwargs
        )


class AlertModal(Modal):
    """Alert modal with OK button."""

    def __init__(self, title: str = "Alert", message: str = "", **kwargs) -> None:
        """Initialize alert modal.

        Args:
            title: Dialog title
            message: Alert message
            **kwargs: Additional arguments
        """
        super().__init__(
            title=title,
            message=message,
            buttons=[("OK", "ok")],
            **kwargs
        )
