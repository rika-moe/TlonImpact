"""Toast notification component for Tlon TUI."""

from textual.widgets import Static
from textual.reactive import reactive
from textual.timer import Timer
from enum import Enum


class ToastLevel(Enum):
    """Toast notification levels."""
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


class Toast(Static):
    """Toast notification component."""

    DEFAULT_CSS = """
    Toast {
        width: auto;
        height: auto;
        padding: 0 2;
        content-align: center middle;
        color: $text;
        dock: bottom;
        layer: notification;
        margin: 1;
    }
    Toast.info {
        background: $primary-darken-2;
    }
    Toast.success {
        background: $success-darken-2;
    }
    Toast.warning {
        background: $warning-darken-2;
        color: $text;
    }
    Toast.error {
        background: $error-darken-2;
    }
    """

    level: reactive[ToastLevel] = reactive(ToastLevel.INFO)

    def __init__(
        self,
        message: str,
        level: ToastLevel = ToastLevel.INFO,
        duration: float = 3.0,
        **kwargs
    ) -> None:
        """Initialize toast notification.

        Args:
            message: Notification message
            level: Notification level
            duration: Display duration in seconds
            **kwargs: Additional arguments
        """
        super().__init__(**kwargs)
        self.message = message
        self.level = level
        self.duration = duration
        self._timer: Timer | None = None

    def compose(self):
        """Compose the toast."""
        self.update(self.message)
        self.add_class(self.level.value)

    def on_mount(self) -> None:
        """Start dismiss timer when mounted."""
        self._timer = self.set_timer(self.duration, self.dismiss)

    def dismiss(self) -> None:
        """Dismiss the toast."""
        if self._timer:
            self._timer.stop()
        self.remove()

    @classmethod
    def info(cls, message: str, duration: float = 3.0) -> "Toast":
        """Create info toast.

        Args:
            message: Notification message
            duration: Display duration

        Returns:
            Toast instance
        """
        return cls(message, ToastLevel.INFO, duration)

    @classmethod
    def success(cls, message: str, duration: float = 3.0) -> "Toast":
        """Create success toast.

        Args:
            message: Notification message
            duration: Display duration

        Returns:
            Toast instance
        """
        return cls(message, ToastLevel.SUCCESS, duration)

    @classmethod
    def warning(cls, message: str, duration: float = 3.0) -> "Toast":
        """Create warning toast.

        Args:
            message: Notification message
            duration: Display duration

        Returns:
            Toast instance
        """
        return cls(message, ToastLevel.WARNING, duration)

    @classmethod
    def error(cls, message: str, duration: float = 5.0) -> "Toast":
        """Create error toast.

        Args:
            message: Notification message
            duration: Display duration (longer for errors)

        Returns:
            Toast instance
        """
        return cls(message, ToastLevel.ERROR, duration)
