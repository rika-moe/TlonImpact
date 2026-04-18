"""Config view for Operator TUI.

Displays and edits agent configuration with:
- Agent config editor (Name, Role, Model)
- Parameter sliders (Temperature, Max Tokens, Timeout)
- Tool toggles (Checkbox)
- System Prompt editor (TextArea)
- Save and Revert actions
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.binding import Binding
from textual.containers import Grid, Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import (
    Button,
    Checkbox,
    Input,
    Label,
    Select,
    Static,
    TextArea,
)

from tlon.app.tui.common.components.modal import ConfirmModal
from tlon.app.tui.operator.views.base import BaseView

if TYPE_CHECKING:
    from textual.app import ComposeResult


class ConfigView(BaseView):
    """Config view - displays agent configuration editor.

    Provides a complete interface for editing agent configuration:
    - Basic fields: Name, Role, Model
    - Parameters: Temperature, Max Tokens, Timeout
    - Tool toggles with checkboxes
    - System prompt multi-line editor
    - Save and revert actions with keyboard shortcuts
    """

    DEFAULT_CSS = """
    ConfigView {
        layout: vertical;
        padding: 1;
    }
    ConfigView .config-header {
        height: 1;
        text-style: bold;
        color: $text-accent;
        margin-bottom: 1;
    }
    ConfigView .config-toolbar {
        height: auto;
        align: right middle;
        margin-bottom: 1;
    }
    ConfigView .config-toolbar Button {
        margin: 0 0 0 1;
    }
    ConfigView .config-form {
        height: 1fr;
        overflow: auto;
    }
    ConfigView .form-section {
        margin: 1 0;
        padding: 0 1;
    }
    ConfigView .section-title {
        text-style: bold;
        color: $text-accent;
        margin-bottom: 1;
    }
    ConfigView .form-row {
        height: auto;
        margin: 1 0;
        align: left middle;
    }
    ConfigView .form-label {
        width: 15;
        color: $text-muted;
    }
    ConfigView .form-input {
        width: 1fr;
    }
    ConfigView .form-value {
        width: 10;
        text-align: right;
    }
    ConfigView .slider-container {
        width: 1fr;
        height: auto;
    }
    ConfigView .slider-track {
        width: 100%;
        height: 1;
        background: $primary-darken-2;
        color: $primary;
    }
    ConfigView .tools-grid {
        grid-size: 3;
        grid-gutter: 1;
        height: auto;
    }
    ConfigView .tool-checkbox {
        margin: 0;
    }
    ConfigView .prompt-container {
        height: auto;
        margin-top: 1;
    }
    ConfigView .prompt-textarea {
        height: 10;
        border: solid $primary;
    }
    ConfigView .status-valid {
        color: $success;
    }
    ConfigView .status-dirty {
        color: $warning;
    }
    ConfigView .config-status {
        width: auto;
        margin-left: 1;
    }
    """

    BINDINGS = [
        Binding("ctrl+s", "save_config", "Save", show=True),
        Binding("ctrl+r", "revert_config", "Revert", show=True),
    ]

    # Reactive state tracking whether config has unsaved changes
    is_dirty: reactive[bool] = reactive(False)

    def __init__(self, **kwargs) -> None:
        """Initialize the Config view."""
        super().__init__(view_id="config", **kwargs)
        self._original_config: dict = {}
        self._current_config: dict = {}

    def render_content(self) -> ComposeResult:
        """Render the Config view content.

        Yields:
            ComposeResult: The composed widgets
        """
        yield Label("Configuration Editor", classes="config-header")

        with Horizontal(classes="config-toolbar"):
            yield Static("[Valid]", classes="config-status status-valid", id="config-status")
            yield Button("Save (s)", id="btn-save", variant="primary")
            yield Button("Revert (r)", id="btn-revert")

        with Vertical(classes="config-form"):
            # Basic info section
            with Vertical(classes="form-section"):
                yield Label("Agent Info", classes="section-title")

                with Horizontal(classes="form-row"):
                    yield Label("Name:", classes="form-label")
                    yield Input(placeholder="agent-name", id="input-name", classes="form-input")

                with Horizontal(classes="form-row"):
                    yield Label("Role:", classes="form-label")
                    yield Input(placeholder="developer", id="input-role", classes="form-input")

                with Horizontal(classes="form-row"):
                    yield Label("Model:", classes="form-label")
                    yield Select(
                        [(model, model) for model in self._get_model_options()],
                        id="select-model",
                        classes="form-input",
                    )

            # Parameters section
            with Vertical(classes="form-section"):
                yield Label("Parameters", classes="section-title")

                with Horizontal(classes="form-row"):
                    yield Label("Temperature:", classes="form-label")
                    yield Input(value="0.7", id="input-temperature", classes="form-input")

                with Horizontal(classes="form-row"):
                    yield Label("Max Tokens:", classes="form-label")
                    yield Input(value="4096", id="input-max-tokens", classes="form-input")

                with Horizontal(classes="form-row"):
                    yield Label("Timeout:", classes="form-label")
                    yield Input(value="30", id="input-timeout", classes="form-input")

            # Tools section
            with Vertical(classes="form-section"):
                yield Label("Tools", classes="section-title")
                with Grid(classes="tools-grid"):
                    yield Checkbox(
                        "file_read", value=True, id="tool-file-read", classes="tool-checkbox"
                    )
                    yield Checkbox(
                        "file_write", value=True, id="tool-file-write", classes="tool-checkbox"
                    )
                    yield Checkbox(
                        "execute_bash", value=True, id="tool-execute-bash", classes="tool-checkbox"
                    )
                    yield Checkbox("search", value=True, id="tool-search", classes="tool-checkbox")
                    yield Checkbox(
                        "database", value=False, id="tool-database", classes="tool-checkbox"
                    )
                    yield Checkbox(
                        "browser", value=False, id="tool-browser", classes="tool-checkbox"
                    )

            # System Prompt section
            with Vertical(classes="form-section"):
                yield Label("System Prompt", classes="section-title")
                with Vertical(classes="prompt-container"):
                    yield TextArea(
                        "You are a senior software engineer...",
                        id="textarea-prompt",
                        classes="prompt-textarea",
                    )

    async def on_mount(self) -> None:
        """Handle mount event."""
        await super().on_mount()
        await self._load_data()

    async def _load_data(self) -> None:
        """Load config data with mock configuration."""
        self._original_config = self._get_mock_config()
        self._current_config = dict(self._original_config)
        self._populate_form()
        self.is_dirty = False

    def _get_mock_config(self) -> dict:
        """Return mock configuration data.

        Returns:
            Dictionary with mock agent configuration
        """
        return {
            "name": "dev-001",
            "role": "Developer",
            "model": "GPT-4",
            "temperature": 0.7,
            "max_tokens": 4096,
            "timeout": 30,
            "tools": {
                "file_read": True,
                "file_write": True,
                "execute_bash": True,
                "search": True,
                "database": False,
                "browser": False,
            },
            "system_prompt": (
                "You are a senior software engineer with expertise in Python, "
                "distributed systems, and AI infrastructure. You write clean, "
                "well-tested code and provide thoughtful code reviews."
            ),
        }

    def _get_model_options(self) -> list[str]:
        """Return available model options.

        Returns:
            List of model names
        """
        return [
            "GPT-4",
            "GPT-4o",
            "GPT-3.5-Turbo",
            "Claude-3-Opus",
            "Claude-3-Sonnet",
            "Local-Llama",
        ]

    def _populate_form(self) -> None:
        """Populate form widgets from current config."""
        config = self._current_config

        name_input = self.query_one("#input-name", Input)
        name_input.value = config.get("name", "")

        role_input = self.query_one("#input-role", Input)
        role_input.value = config.get("role", "")

        model_select = self.query_one("#select-model", Select)
        model_value = config.get("model", "GPT-4")
        if model_value in self._get_model_options():
            model_select.value = model_value

        temp_input = self.query_one("#input-temperature", Input)
        temp_input.value = str(config.get("temperature", 0.7))

        tokens_input = self.query_one("#input-max-tokens", Input)
        tokens_input.value = str(config.get("max_tokens", 4096))

        timeout_input = self.query_one("#input-timeout", Input)
        timeout_input.value = str(config.get("timeout", 30))

        tools = config.get("tools", {})
        self.query_one("#tool-file-read", Checkbox).value = tools.get("file_read", False)
        self.query_one("#tool-file-write", Checkbox).value = tools.get("file_write", False)
        self.query_one("#tool-execute-bash", Checkbox).value = tools.get("execute_bash", False)
        self.query_one("#tool-search", Checkbox).value = tools.get("search", False)
        self.query_one("#tool-database", Checkbox).value = tools.get("database", False)
        self.query_one("#tool-browser", Checkbox).value = tools.get("browser", False)

        prompt_area = self.query_one("#textarea-prompt", TextArea)
        prompt_area.text = config.get("system_prompt", "")

    def _collect_form_data(self) -> dict:
        """Collect form data into config dictionary.

        Returns:
            Dictionary with current form values
        """
        errors: list[str] = []

        try:
            temperature = float(self.query_one("#input-temperature", Input).value)
            if not (0.0 <= temperature <= 2.0):
                raise ValueError("Temperature must be between 0.0 and 2.0")
        except ValueError as e:
            errors.append(str(e) if str(e) != "" else "Invalid temperature value")
            temperature = self._original_config.get("temperature", 0.7)

        try:
            max_tokens = int(self.query_one("#input-max-tokens", Input).value)
            if max_tokens <= 0:
                raise ValueError("Max tokens must be positive")
        except ValueError as e:
            errors.append(str(e) if str(e) != "" else "Invalid max tokens value")
            max_tokens = self._original_config.get("max_tokens", 4096)

        try:
            timeout = int(self.query_one("#input-timeout", Input).value)
            if timeout <= 0:
                raise ValueError("Timeout must be positive")
        except ValueError as e:
            errors.append(str(e) if str(e) != "" else "Invalid timeout value")
            timeout = self._original_config.get("timeout", 30)

        if errors:
            self.app.notify(f"Input validation errors: {', '.join(errors)}", severity="error")

        return {
            "name": self.query_one("#input-name", Input).value,
            "role": self.query_one("#input-role", Input).value,
            "model": self.query_one("#select-model", Select).value or "GPT-4",
            "temperature": temperature,
            "max_tokens": max_tokens,
            "timeout": timeout,
            "tools": {
                "file_read": self.query_one("#tool-file-read", Checkbox).value,
                "file_write": self.query_one("#tool-file-write", Checkbox).value,
                "execute_bash": self.query_one("#tool-execute-bash", Checkbox).value,
                "search": self.query_one("#tool-search", Checkbox).value,
                "database": self.query_one("#tool-database", Checkbox).value,
                "browser": self.query_one("#tool-browser", Checkbox).value,
            },
            "system_prompt": self.query_one("#textarea-prompt", TextArea).text,
        }

    def _check_dirty(self) -> None:
        """Check if current form differs from original config."""
        current = self._collect_form_data()
        self.is_dirty = current != self._original_config

    def watch_is_dirty(self, is_dirty: bool) -> None:
        """Watch for dirty state changes and update status label.

        Args:
            is_dirty: Whether there are unsaved changes
        """
        status = self.query_one("#config-status", Static)
        if is_dirty:
            status.update("[Modified]")
            status.remove_class("status-valid")
            status.add_class("status-dirty")
        else:
            status.update("[Valid]")
            status.remove_class("status-dirty")
            status.add_class("status-valid")

    def on_input_changed(self, event: Input.Changed) -> None:
        """Handle input changes to track dirty state."""
        self._check_dirty()

    def on_select_changed(self, event: Select.Changed) -> None:
        """Handle select changes to track dirty state."""
        self._check_dirty()

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        """Handle checkbox changes to track dirty state."""
        self._check_dirty()

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        """Handle textarea changes to track dirty state."""
        self._check_dirty()

    def action_save_config(self) -> None:
        """Action to save the current configuration."""
        self._current_config = self._collect_form_data()
        self._original_config = dict(self._current_config)
        self.is_dirty = False
        self.app.notify("Configuration saved", severity="information")

    def action_revert_config(self) -> None:
        """Action to revert configuration to original values."""
        if not self.is_dirty:
            self.app.notify("No changes to revert", severity="warning")
            return

        def handle_confirm(result: str) -> None:
            if result == "yes":
                self._current_config = dict(self._original_config)
                self._populate_form()
                self.is_dirty = False
                self.app.notify("Configuration reverted", severity="information")

        self.app.push_screen(
            ConfirmModal(
                title="Revert Changes",
                message="Are you sure you want to discard all unsaved changes?",
            ),
            handle_confirm,
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button press events.

        Args:
            event: Button press event
        """
        button_id = event.button.id
        if button_id == "btn-save":
            self.action_save_config()
        elif button_id == "btn-revert":
            self.action_revert_config()
