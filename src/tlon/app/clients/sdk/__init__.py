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
