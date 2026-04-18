"""InputBox - Message input component.

Multi-line input with:
- Shift+Enter for new lines
- Enter to send
- Input history navigation
"""

from __future__ import annotations

from textual.containers import Vertical
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Input


class InputBox(Vertical):
    """Message input box component.

    Provides a text input area with event handling for:
    - Text changes
    - Send action (Enter)
    - New line (Shift+Enter)
    """

    DEFAULT_CSS = """
    InputBox {
        width: 1fr;
        height: auto;
        min-height: 3;
        max-height: 10;
    }

    InputBox Input {
        width: 100%;
        height: auto;
        min-height: 3;
        border: solid $primary-darken-2;
    }

    InputBox Input:focus {
        border: solid $primary;
    }

    InputBox TextArea {
        width: 100%;
        height: auto;
        min-height: 3;
        max-height: 8;
        border: solid $primary-darken-2;
    }

    InputBox TextArea:focus {
        border: solid $primary;
    }
    """

    class Changed(Message):
        """Message sent when input text changes."""

        def __init__(self, value: str) -> None:
            """Initialize message.

            Args:
                value: Current input value
            """
            super().__init__()
            self.value = value

    class Send(Message):
        """Message sent when user wants to send."""

        def __init__(self, value: str) -> None:
            """Initialize message.

            Args:
                value: Text to send
            """
            super().__init__()
            self.value = value

    value: reactive[str] = reactive("")

    def __init__(self, *args, **kwargs):
        """Initialize input box."""
        super().__init__(*args, **kwargs)
        self._use_text_area = False  # Use Input for simplicity

    def compose(self):
        """Compose input box."""
        yield Input(placeholder="输入消息... (Enter发送, /查看命令)", id="input")

    def get_input(self) -> str:
        """Get current input text.

        Returns:
            Current input value
        """
        input_widget = self.query_one("#input", Input)
        return input_widget.value

    def set_input(self, value: str) -> None:
        """Set input text.

        Args:
            value: Text to set
        """
        input_widget = self.query_one("#input", Input)
        input_widget.value = value
        input_widget.focus()

    def clear(self) -> None:
        """Clear input text."""
        input_widget = self.query_one("#input", Input)
        input_widget.value = ""
        input_widget.focus()

    def focus(self) -> None:
        """Focus the input widget."""
        input_widget = self.query_one("#input", Input)
        input_widget.focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        """Handle input changes."""
        self.value = event.value
        self.post_message(self.Changed(event.value))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle input submission (Enter key)."""
        self.post_message(self.Send(event.value))
