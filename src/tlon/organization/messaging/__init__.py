"""Messaging infrastructure for Tlon.

This module contains the messaging infrastructure components:
- EventBus: Async pub-sub event routing
- ChatRoom: Chat room state and participant management
"""

from .congress import Congress, Participant, CongressMetadata

__all__ = ["Congress", "Participant"]
