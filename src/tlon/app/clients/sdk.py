"""Tlon Client SDK - Backward compatibility module.

This module re-exports the new SDK for backward compatibility.
New code should import from tlon.app.clients.sdk directly.

Deprecated: Use tlon.app.clients.sdk instead.
"""

# Re-export all public symbols from new SDK
from tlon.app.clients.sdk import (
    # Main client
    TlonClient,
    # Base classes
    BaseClient,
    ClientAdapter,
    ConnectionState,
    # Adapters
    TerminalAdapter,
    WebAdapter,
    # Exceptions
    TlonClientError,
    ConnectionError,
    AuthenticationError,
    ProtocolError,
)

__all__ = [
    "TlonClient",
    "BaseClient",
    "ClientAdapter",
    "ConnectionState",
    "TerminalAdapter",
    "WebAdapter",
    "TlonClientError",
    "ConnectionError",
    "AuthenticationError",
    "ProtocolError",
]
