"""Unit tests for Operator TUI.

Tests cover:
- View imports and initialization
- Component imports and initialization
- App initialization
"""

from unittest.mock import MagicMock, patch

import pytest


class TestOperatorViews:
    """Tests for Operator views."""

    def test_base_view_import(self):
        """Test BaseView can be imported."""
        from tlon.app.tui.operator.views.base import BaseView

        assert BaseView is not None

    def test_agents_view_import(self):
        """Test AgentsView can be imported."""
        from tlon.app.tui.operator.views.agents import AgentsView

        assert AgentsView is not None

    def test_events_view_import(self):
        """Test EventsView can be imported."""
        from tlon.app.tui.operator.views.events import EventsView

        assert EventsView is not None

    def test_logs_view_import(self):
        """Test LogsView can be imported."""
        from tlon.app.tui.operator.views.logs import LogsView

        assert LogsView is not None

    def test_traces_view_import(self):
        """Test TracesView can be imported."""
        from tlon.app.tui.operator.views.traces import TracesView

        assert TracesView is not None

    def test_config_view_import(self):
        """Test ConfigView can be imported."""
        from tlon.app.tui.operator.views.config import ConfigView

        assert ConfigView is not None


class TestOperatorComponents:
    """Tests for Operator components."""

    def test_navigation_bar_import(self):
        """Test NavigationBar can be imported."""
        from tlon.app.tui.operator.components.navigation import NavigationBar

        assert NavigationBar is not None

    def test_sidebar_import(self):
        """Test Sidebar can be imported."""
        from tlon.app.tui.operator.components.sidebar import Sidebar

        assert Sidebar is not None

    def test_inspector_panel_import(self):
        """Test InspectorPanel can be imported."""
        from tlon.app.tui.operator.components.inspector import InspectorPanel

        assert InspectorPanel is not None

    def test_mini_event_stream_import(self):
        """Test MiniEventStream can be imported."""
        from tlon.app.tui.operator.components.sidebar import MiniEventStream

        assert MiniEventStream is not None


class TestOperatorApp:
    """Tests for OperatorApp."""

    def test_app_import(self):
        """Test OperatorApp can be imported."""
        from tlon.app.tui.operator.app import OperatorApp

        assert OperatorApp is not None

    def test_app_initialization(self):
        """Test OperatorApp initialization."""
        from tlon.app.tui.operator.app import OperatorApp

        app = OperatorApp(
            host="127.0.0.1",
            ws_port=8888,
            rest_port=8080,
        )

        assert app.host == "127.0.0.1"
        assert app.ws_port == 8888
        assert app.rest_port == 8080

    def test_app_bindings(self):
        """Test OperatorApp has correct key bindings."""
        from tlon.app.tui.operator.app import OperatorApp

        app = OperatorApp()

        # Check that bindings are defined
        binding_keys = [b.key for b in app.BINDINGS]
        assert "1" in binding_keys
        assert "2" in binding_keys
        assert "3" in binding_keys
        assert "4" in binding_keys
        assert "5" in binding_keys
        assert "r" in binding_keys
        assert "q" in binding_keys
        assert "?" in binding_keys


class TestTextualClient:
    """Tests for Textual client module."""

    def test_tlon_textual_app_import(self):
        """Test TlonTextualApp can be imported."""
        from tlon.app.clients.textual import TlonTextualApp

        assert TlonTextualApp is not None

    def test_tlon_textual_app_is_operator_app(self):
        """Test TlonTextualApp is an alias for OperatorApp."""
        from tlon.app.clients.textual import TlonTextualApp
        from tlon.app.tui.operator.app import OperatorApp

        assert TlonTextualApp is OperatorApp
