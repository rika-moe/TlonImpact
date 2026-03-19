"""Tlon Client SDK - Refactored with Adapter Pattern.

This package provides a unified client SDK for connecting to the Tlon Server
with support for multiple client types through the Adapter pattern.

Usage:
    >>> from tlon.app.clients.sdk import TlonClient, TerminalAdapter
    >>>
    >>> async with TlonClient("ws://localhost:8888/ws", adapter=TerminalAdapter()) as client:
    ...     await client.authenticate()
    ...     await client.send_message("Hello!")

Architecture:
    - BaseClient: Abstract interface for WebSocket connection management
    - ClientAdapter: Abstract interface for client-specific behavior
    - TlonClient: Unified entry point combining BaseClient + Adapter
    - TerminalAdapter: Adapter for TUI clients (receives all events)
    - WebAdapter: Adapter for Web clients (filtered events)
"""

from .adapter import ClientAdapter, TerminalAdapter, WebAdapter
from .base_client import BaseClient, ConnectionState
from .client import TlonClient
from .exceptions import (
    AuthenticationError,
    ConnectionError,
    ProtocolError,
    TlonClientError,
)

__all__ = [
    # Main client
    "TlonClient",
    # Base classes
    "BaseClient",
    "ClientAdapter",
    "ConnectionState",
    # Adapters
    "TerminalAdapter",
    "WebAdapter",
    # Exceptions
    "TlonClientError",
    "ConnectionError",
    "AuthenticationError",
    "ProtocolError",
]
