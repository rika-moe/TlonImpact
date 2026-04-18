"""Color definitions for Tlon TUI."""

from dataclasses import dataclass
from enum import Enum


@dataclass(frozen=True)
class ThemeColors:
    """Theme color definitions."""

    # Primary colors
    PRIMARY: str = "#3B82F6"
    PRIMARY_LIGHT: str = "#60A5FA"
    PRIMARY_DARK: str = "#1D4ED8"

    # Secondary colors
    SECONDARY: str = "#6B7280"
    SECONDARY_LIGHT: str = "#9CA3AF"
    SECONDARY_DARK: str = ""

    # Background colors
    BG_PRIMARY: str = "#0F172A"
    BG_SECONDARY: str = "#1E293B"
    BG_TERTIARY: str = ""

    # Text colors
    TEXT_PRIMARY: str = "#F8FAFC"
    TEXT_SECONDARY: str = "#94A3B8"
    TEXT_MUTED: str = "#64748B"

    # Accent colors
    ACCENT_BLUE: str = "#3B82F6"
    ACCENT_GREEN: str = "#10B981"
    ACCENT_YELLOW: str = "#F59E0B"
    ACCENT_RED: str = "#EF4444"
    ACCENT_PURPLE: str = "#8B5CF6"


class AgentStatusColors(Enum):
    """Agent status colors."""

    HEALTHY = ("#10B981", "●")  # Green
    IDLE = ("#6B7280", "○")  # Gray
    BUSY = ("#F59E0B", "◐")  # Yellow
    DEGRADED = ("#8B5CF6", "◌")  # Purple
    ERROR = ("#EF4444", "✕")  # Red
    PAUSED = ("#3B82F6", "⏸")  # Blue
    OFFLINE = ("", "○")  # Dark gray

    def __init__(self, color: str, icon: str):
        self.color = color
        self.icon = icon


class LogLevelColors(Enum):
    """Log level colors."""

    DEBUG = ("#6B7280", "🔍")  # Gray
    INFO = ("#3B82F6", "ℹ️")  # Blue
    WARNING = ("#F59E0B", "⚠️")  # Yellow
    ERROR = ("#EF4444", "❌")  # Red
    FATAL = ("#DC2626", "💀")  # Dark Red

    def __init__(self, color: str, icon: str):
        self.color = color
        self.icon = icon


# Message type colors
MESSAGE_TYPE_COLORS = {
    "user": "#3B82F6",  # Blue
    "agent": "#10B981",  # Green
    "system": "#6B7280",  # Gray
    "error": "#EF4444",  # Red
}

# Agent role emoji mapping
AGENT_ROLE_EMOJI = {
    "developer": "💻",
    "dev": "💻",
    "开发工程师": "💻",
    "开发": "💻",
    "pm": "👔",
    "product_manager": "👔",
    "test": "🧪",
    "tester": "🧪",
    "review": "📋",
    "reviewer": "📋",
    "designer": "🎨",
    "ops": "🔧",
    "system": "⚙️",
    "default": "🤖",
}


def get_agent_emoji(role: str) -> str:
    """Get emoji for agent role.

    Args:
        role: Agent role string

    Returns:
        Emoji character
    """
    return AGENT_ROLE_EMOJI.get(role.lower(), AGENT_ROLE_EMOJI["default"])


def get_status_color(status: str) -> str:
    """Get color for agent status.

    Args:
        status: Agent status string

    Returns:
        Color hex code
    """
    try:
        return AgentStatusColors[status.upper()].color
    except KeyError:
        return AgentStatusColors.OFFLINE.color


def get_status_icon(status: str) -> str:
    """Get icon for agent status.

    Args:
        status: Agent status string

    Returns:
        Icon character
    """
    try:
        return AgentStatusColors[status.upper()].icon
    except KeyError:
        return AgentStatusColors.OFFLINE.icon
