"""Congress TUI State Management.

Provides data classes for managing the state of the Congress TUI,
including messages, agents, and UI state.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class MessageType(Enum):
    """Message types in Congress TUI."""

    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"


class AgentState(Enum):
    """Agent connection/working state."""

    IDLE = "idle"
    ACTIVE = "active"
    BUSY = "busy"
    OFFLINE = "offline"
    THINKING = "thinking"


@dataclass
class Message:
    """A message in the Congress TUI.

    Attributes:
        id: Unique message identifier
        content: Message content text
        sender_type: Type of sender (user, agent, system)
        sender_name: Display name of sender
        sender_id: Optional sender identifier
        timestamp: Message timestamp
        mentions: List of mentioned agent IDs
        reply_to: Optional ID of message being replied to
        metadata: Additional message metadata
    """

    content: str
    sender_type: MessageType
    sender_name: str
    id: str | None = None
    sender_id: str | None = None
    timestamp: datetime | None = None
    mentions: list[str] = field(default_factory=list)
    reply_to: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """Set default timestamp if not provided."""
        if self.timestamp is None:
            self.timestamp = datetime.now()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Message":
        """Create Message from dictionary.

        Args:
            data: Dictionary containing message data

        Returns:
            Message instance
        """
        sender_type = data.get("sender_type", "agent")
        if isinstance(sender_type, str):
            # Map server-side sender types to Congress MessageType
            sender_type_map = {
                "user": MessageType.USER,
                "human": MessageType.USER,
                "agent": MessageType.AGENT,
                "system": MessageType.SYSTEM,
            }
            sender_type = sender_type_map.get(sender_type.lower(), MessageType.AGENT)

        timestamp = data.get("timestamp")
        if isinstance(timestamp, (int, float)):
            timestamp = datetime.fromtimestamp(timestamp)
        elif isinstance(timestamp, str):
            try:
                timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            except ValueError:
                timestamp = datetime.now()

        return cls(
            id=data.get("id"),
            content=data.get("content", ""),
            sender_type=sender_type,
            sender_name=data.get("sender_name", "Unknown"),
            sender_id=data.get("sender_id"),
            timestamp=timestamp,
            mentions=data.get("mentions", []),
            reply_to=data.get("reply_to"),
            metadata=data.get("metadata", {}),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert Message to dictionary.

        Returns:
            Dictionary representation of the message
        """
        return {
            "id": self.id,
            "content": self.content,
            "sender_type": self.sender_type.value,
            "sender_name": self.sender_name,
            "sender_id": self.sender_id,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "mentions": self.mentions,
            "reply_to": self.reply_to,
            "metadata": self.metadata,
        }


@dataclass
class AgentInfo:
    """Information about an Agent in Congress.

    Attributes:
        id: Unique agent identifier
        name: Display name
        role: Agent role description
        state: Current agent state
        emoji: Emoji representation
        color: Color for display
        capabilities: List of agent capabilities
        metadata: Additional agent metadata
    """

    id: str = "unknown"
    name: str = "Unknown"
    role: str = ""
    state: AgentState = field(default=AgentState.IDLE)
    emoji: str = "🤖"
    color: str = "cyan"
    capabilities: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """Set default emoji based on role/state."""
        if self.emoji == "🤖" and self.role:
            role_emojis = {
                "dev": "💻",
                "developer": "💻",
                "pm": "📊",
                "test": "🧪",
                "review": "👀",
                "ops": "🔧",
                "design": "🎨",
            }
            role_lower = self.role.lower()
            for key, emoji in role_emojis.items():
                if key in role_lower:
                    self.emoji = emoji
                    break

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentInfo":
        """Create AgentInfo from dictionary.

        Args:
            data: Dictionary containing agent data

        Returns:
            AgentInfo instance
        """
        state = data.get("state", "idle")
        if isinstance(state, str):
            try:
                state = AgentState(state)
            except ValueError:
                state = AgentState.IDLE

        return cls(
            id=data.get("id", "unknown"),
            name=data.get("name", "Unknown"),
            role=data.get("role", ""),
            state=state,
            emoji=data.get("emoji", "🤖"),
            color=data.get("color", "cyan"),
            capabilities=data.get("capabilities", []),
            metadata=data.get("metadata", {}),
        )

    @property
    def status_icon(self) -> str:
        """Get status icon based on agent state.

        Returns:
            Status icon emoji
        """
        icons = {
            AgentState.IDLE: "🟢",
            AgentState.ACTIVE: "🔵",
            AgentState.BUSY: "🟠",
            AgentState.OFFLINE: "⚪",
            AgentState.THINKING: "💭",
        }
        return icons.get(self.state, "⚪")

    @property
    def display_name(self) -> str:
        """Get formatted display name.

        Returns:
            Formatted display name with emoji
        """
        return f"{self.emoji} {self.name}"


@dataclass
class CongressState:
    """Core state for Congress TUI.

    Manages all mutable state for the Congress TUI interface,
    including messages, agents, and UI state.

    Attributes:
        current_agent: Currently selected agent ID
        messages: List of messages in the conversation
        input_history: History of user inputs
        is_typing: Whether an agent is currently typing
        agents: Dictionary of online agents by ID
        connection_state: Current WebSocket connection state
        current_trace_id: Optional trace ID for filtering
    """

    current_agent: str | None = None
    messages: list[Message] = field(default_factory=list)
    input_history: list[str] = field(default_factory=list)
    typing_agents: set[str] = field(default_factory=set)
    agents: dict[str, AgentInfo] = field(default_factory=dict)
    connection_state: str = "disconnected"
    current_trace_id: str | None = None

    def add_message(self, message: Message) -> None:
        """Add a message to the conversation.

        Args:
            message: Message to add
        """
        self.messages.append(message)

    def add_input_to_history(self, text: str) -> None:
        """Add input to history.

        Args:
            text: Input text to add
        """
        if text.strip():
            self.input_history.append(text.strip())

    def update_agent(self, agent_info: AgentInfo) -> None:
        """Update or add an agent.

        Args:
            agent_info: Agent information to update
        """
        self.agents[agent_info.id] = agent_info

    def remove_agent(self, agent_id: str) -> None:
        """Remove an agent from the list.

        Args:
            agent_id: ID of agent to remove
        """
        if agent_id in self.agents:
            del self.agents[agent_id]
            if self.current_agent == agent_id:
                self.current_agent = None

    @property
    def is_typing(self) -> bool:
        """Check if any agent is currently typing."""
        return len(self.typing_agents) > 0

    def set_typing(self, agent_id: str | None, is_typing: bool) -> None:
        """Set typing state for an agent.

        Args:
            agent_id: ID of agent typing (or None to clear)
            is_typing: Whether the agent is typing
        """
        if not agent_id or agent_id not in self.agents:
            return

        if is_typing:
            self.typing_agents.add(agent_id)
            self.agents[agent_id].state = AgentState.THINKING
        else:
            self.typing_agents.discard(agent_id)
            self.agents[agent_id].state = AgentState.IDLE

    def get_agent_list(self) -> list[AgentInfo]:
        """Get sorted list of agents.

        Returns:
            List of agents sorted by state (idle first, then busy, then offline)
        """
        state_order = {
            AgentState.IDLE: 0,
            AgentState.ACTIVE: 1,
            AgentState.THINKING: 2,
            AgentState.BUSY: 3,
            AgentState.OFFLINE: 4,
        }
        return sorted(
            self.agents.values(),
            key=lambda a: state_order.get(a.state, 5),
        )

    def get_last_message(self) -> Message | None:
        """Get the last message in the conversation.

        Returns:
            Last message or None if no messages
        """
        return self.messages[-1] if self.messages else None

    def clear_messages(self) -> None:
        """Clear all messages."""
        self.messages.clear()

    def get_messages_by_sender(self, sender_id: str) -> list[Message]:
        """Get all messages from a specific sender.

        Args:
            sender_id: ID of sender

        Returns:
            List of messages from that sender
        """
        return [m for m in self.messages if m.sender_id == sender_id]
