"""ChatView - Scrollable chat message area.

Displays message history with auto-scrolling to latest messages.
"""

from __future__ import annotations

from textual.containers import VerticalScroll
from textual.widgets import Static

from .message_bubble import MessageBubble, MessageType


class ChatView(VerticalScroll):
    """Chat message view with scrolling.

    Displays a scrollable list of messages with auto-scroll
    to the latest message.
    """

    DEFAULT_CSS = """
    ChatView {
        width: 100%;
        height: 1fr;
        background: $surface-darken-1;
        border: solid $primary-darken-3;
        padding: 1;
    }

    ChatView:focus {
        border: solid $primary;
    }

    ChatView .empty-message {
        text-align: center;
        color: $text-muted;
        text-style: dim;
        margin-top: 50%;
    }
    """

    def __init__(self, *args, **kwargs):
        """Initialize chat view."""
        super().__init__(*args, **kwargs)
        self._messages: list[MessageBubble] = []
        self._empty_label: Static | None = None

    def compose(self):
        """Compose chat view."""
        self._empty_label = Static(
            "暂无消息，开始对话吧...",
            classes="empty-message",
        )
        yield self._empty_label

    def add_message(
        self,
        content: str,
        sender_name: str,
        sender_type: MessageType,
        timestamp: str = "",
        avatar: str = "🤖",
    ) -> None:
        """Add a message to the chat.

        Args:
            content: Message content
            sender_name: Display name of sender
            sender_type: Type of sender
            timestamp: Time string
            avatar: Avatar emoji for agents
        """
        # Remove empty message indicator
        if self._empty_label and self._empty_label.parent:
            self._empty_label.remove()
            self._empty_label = None

        # Create message bubble
        bubble = MessageBubble(
            content=content,
            sender_name=sender_name,
            sender_type=sender_type,
            timestamp=timestamp,
            avatar=avatar,
        )

        # Mount and scroll to bottom
        self.mount(bubble)
        self._messages.append(bubble)

        # Auto-scroll to bottom
        self.scroll_end(animate=False)

    def clear_messages(self) -> None:
        """Clear all messages from the chat."""
        # Remove all message bubbles
        for bubble in self._messages:
            bubble.remove()
        self._messages.clear()

        # Show empty message
        if not self._empty_label:
            self._empty_label = Static(
                "暂无消息，开始对话吧...",
                classes="empty-message",
            )
            self.mount(self._empty_label)

    def on_mount(self) -> None:
        """Called when widget is mounted."""
        self.focus()
