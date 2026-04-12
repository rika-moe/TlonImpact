"""Textual TUI client for Tlon.

Provides the TlonTextualApp that wraps the Operator TUI application.
"""

from __future__ import annotations

from tlon.app.tui.operator.app import OperatorApp

# Alias for backward compatibility
TlonTextualApp = OperatorApp

__all__ = ["TlonTextualApp", "OperatorApp"]
