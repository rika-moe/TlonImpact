"""Tlon Client SDK - Refactored with Adapter Pattern.

This package provides a unified client SDK for connecting to the Tlon Server
(protocol v2: REST + SSE) with support for multiple client types through
the Adapter pattern.

Usage:
    >>> from tlon.app.clients.sdk import TlonClient, TerminalAdapter
    >>>
    >>> async with TlonClient("http://localhost:8080", adapter=TerminalAdapter()) as client:
    ...     await client.authenticate()
    ...     await client.send_message("Hello!", to=["agent-1"])

Architecture:
    - BaseClient: REST + SSE thin client (sessions, event stream, reconnect)
    - ClientAdapter: Abstract interface for client-specific behavior
    - TlonClient: Unified entry point combining BaseClient + Adapter
    - TerminalAdapter: Adapter for TUI clients (receives all events)
"""
