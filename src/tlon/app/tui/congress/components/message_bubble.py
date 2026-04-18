"""MessageBubble - Message display component.

Supports:
- User messages: right-aligned, blue bubble
- Agent messages: left-aligned, white bubble, with avatar
- System messages: centered, gray small text
- Code blocks: syntax highlighting, line numbers, copy button
"""

from __future__ import annotations

import re

from rich.syntax import Syntax
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Label, Static

from ..state import MessageType


class BubbleContent(Vertical):
    """Bubble content container with shrink disabled for proper auto-width."""

    DEFAULT_CSS = """
    BubbleContent {
        height: auto;
    }
    """

    shrink = False


class CodeBlock(Static):
    """Code block with syntax highlighting."""

    DEFAULT_CSS = """
    CodeBlock {
        width: 100%;
        height: auto;
        background: $surface-darken-1;
        border: solid $primary-darken-2;
        padding: 0 1;
    }
    """

    def __init__(self, code: str, language: str = "text"):
        """Initialize code block.

        Args:
            code: Code content
            language: Programming language for syntax highlighting
        """
        super().__init__()
        self.code = code
        self.language = language

    def compose(self):
        """Compose code block."""
        # Header with language and copy button
        with Horizontal(classes="code-header"):
            yield Label(f"{self.language}", classes="code-lang")
            yield Button("复制", id="copy-btn", variant="primary")

        # Code content
        yield Static(self._render_code(), classes="code-content")

    def _render_code(self) -> Syntax:
        """Render code with syntax highlighting."""
        return Syntax(
            self.code,
            self.language,
            theme="monokai",
            line_numbers=True,
            word_wrap=True,
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle copy button press."""
        if event.button.id == "copy-btn":
            # Copy to clipboard (would need platform-specific implementation)
            self.app.notify("代码已复制到剪贴板")


class MessageBubble(Vertical):
    """Message bubble component.

    Displays a single message with appropriate styling based on sender type.
    """

    DEFAULT_CSS = """
    MessageBubble {
        width: 100%;
        height: auto;
        margin: 1 0;
    }

    MessageBubble.user {
        align: right middle;
    }

    MessageBubble.agent {
        align: left middle;
    }

    MessageBubble.system {
        align: center middle;
    }

    MessageBubble .bubble-content {
        width: auto;
        max-width: 80%;
        padding: 1 2;
    }

    MessageBubble.user .bubble-content {
        background: $primary-darken-1;
        color: $text;
        border-right: solid $primary;
    }

    MessageBubble.agent .bubble-content {
        background: $surface-lighten-1;
        color: $text;
        border-left: solid $primary;
    }

    MessageBubble.system .bubble-content {
        background: transparent;
        color: $text-muted;
        text-style: dim;
    }

    MessageBubble .sender-name {
        text-style: bold;
        margin-bottom: 1;
    }

    MessageBubble .timestamp {
        color: $text-muted;
        text-style: dim;
        margin-left: 1;
    }

    MessageBubble .avatar {
        width: 3;
        content-align: center middle;
    }

    MessageBubble .text-content {
        width: auto;
        max-width: 100%;
    }

    MessageBubble .spacer {
        width: 1fr;
    }

    MessageBubble .message-row {
        height: auto;
    }
    """

    def __init__(
        self,
        content: str,
        sender_name: str,
        sender_type: MessageType,
        timestamp: str = "",
        avatar: str = "🤖",
    ):
        """Initialize message bubble.

        Args:
            content: Message content
            sender_name: Display name of sender
            sender_type: Type of sender (user/agent/system)
            timestamp: Time string
            avatar: Avatar emoji for agent messages
        """
        super().__init__()
        self.content = content
        self.sender_name = sender_name
        self.sender_type = sender_type
        self.timestamp = timestamp
        self.avatar = avatar

        # Add CSS class based on sender type
        self.add_class(sender_type.value)

    def compose(self):
        """Compose message bubble."""
        if self.sender_type == MessageType.USER:
            # User message: right-aligned
            with Horizontal(classes="message-row"):
                yield Static("", classes="spacer")
                with BubbleContent(classes="bubble-content"):
                    yield Label(f"{self.sender_name} {self.timestamp}", classes="sender-name")
                    yield from self._render_content()

        elif self.sender_type == MessageType.AGENT:
            # Agent message: left-aligned with avatar
            with Horizontal(classes="message-row"):
                yield Label(self.avatar, classes="avatar")
                with BubbleContent(classes="bubble-content"):
                    yield Label(
                        f"{self.sender_name} {self.timestamp}",
                        classes="sender-name",
                    )
                    yield from self._render_content()

        else:
            # System message: centered
            with Horizontal(classes="message-row"):
                yield Label(
                    f"[{self.timestamp}] {self.content}",
                    classes="bubble-content",
                )

    def _render_content(self):
        """Render message content, handling code blocks."""
        # Parse code blocks
        parts = self._parse_content(self.content)

        for part_type, part_content in parts:
            if part_type == "code":
                yield CodeBlock(part_content, language="text")
            else:
                # Regular text
                yield Static(part_content, classes="text-content")

    def _parse_content(self, content: str) -> list[tuple[str, str]]:
        """Parse content into text and code blocks.

        Supports:
        - ```language\ncode\n``` (with language identifier)
        - ```\ncode\n``` (without language identifier)
        - Inline code blocks without trailing newline before ```

        Args:
            content: Raw message content

        Returns:
            List of (type, content) tuples
        """
        parts = []
        current_pos = 0

        # Match code blocks: ```[language]\ncode[\n]```
        # Group 1: optional language, Group 2: code content
        pattern = r"```(?:([\w+]+)\n?)?\n?(.*?)```"

        for match in re.finditer(pattern, content, re.DOTALL):
            # Text before code block
            if match.start() > current_pos:
                text = content[current_pos : match.start()].strip()
                if text:
                    parts.append(("text", text))

            # Code block - strip trailing whitespace/newlines from code
            code = match.group(2).strip("\n")
            parts.append(("code", code))

            current_pos = match.end()

        # Remaining text
        if current_pos < len(content):
            text = content[current_pos:].strip()
            if text:
                parts.append(("text", text))

        # If no code blocks found, return entire content as text
        if not parts:
            parts.append(("text", content))

        return parts
