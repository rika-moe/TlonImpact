"""Tlon protocol definitions.

This module contains shared data structures used across all components
(Server, Agent, ChatRoom, TUI, Web clients).

Design principles:
- Zero external dependencies (stdlib only) to avoid circular imports
- JSON-serializable for WebSocket transmission
- Type-safe with proper deserialization
"""

from .event import Event, EventType
from .message import Message, MessageType

__all__ = ["Event", "EventType", "Message", "MessageType"]
