"""Tlon Textual TUI Client.

Provides a Textual-based TUI interface for connecting to Tlon server.
This module re-exports the OperatorApp as TlonTextualApp for backward compatibility.
"""

from __future__ import annotations

from tlon.app.tui.operator.app import OperatorApp

TlonTextualApp = OperatorApp

__all__ = ["TlonTextualApp"]
