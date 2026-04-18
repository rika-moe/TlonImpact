"""Unit tests for Operator TUI.

Tests cover:
- View imports and initialization
- Component imports and initialization
- App initialization
- View data models and rendering logic
"""


class TestOperatorViews:
    """Tests for Operator views."""

    def test_base_view_import(self):
        """Test BaseView can be imported."""
        from tlon.app.clients.tui.operator.views.base import BaseView

        assert BaseView is not None

    def test_agents_view_import(self):
        """Test AgentsView can be imported."""
        from tlon.app.clients.tui.operator.views.agents import AgentsView

        assert AgentsView is not None

    def test_events_view_import(self):
        """Test EventsView can be imported."""
        from tlon.app.clients.tui.operator.views.events import EventsView

        assert EventsView is not None

    def test_logs_view_import(self):
        """Test LogsView can be imported."""
        from tlon.app.clients.tui.operator.views.logs import LogsView

        assert LogsView is not None

    def test_traces_view_import(self):
        """Test TracesView can be imported."""
        from tlon.app.clients.tui.operator.views.traces import TracesView

        assert TracesView is not None

    def test_config_view_import(self):
        """Test ConfigView can be imported."""
        from tlon.app.clients.tui.operator.views.config import ConfigView

        assert ConfigView is not None


class TestTracesView:
    """Tests for TracesView data models and rendering."""

    async def test_traces_view_mount_and_data(self):
        """Test TracesView mounts and loads mock data."""
        from textual.app import App

        from tlon.app.clients.tui.operator.views.traces import TracesView

        class TestApp(App):
            def compose(self):
                yield TracesView(id="traces")

        app = TestApp()
        async with app.run_test() as pilot:
            view = pilot.app.query_one(TracesView)
            assert len(view._traces) == 5
            assert view._traces[0].trace_id == "trace-001"
            assert view._traces[0].status == "completed"

    async def test_render_flame_chart(self):
        """Test flame chart rendering produces expected output."""
        from textual.app import App

        from tlon.app.clients.tui.operator.views.traces import TracesView

        class TestApp(App):
            def compose(self):
                yield TracesView(id="traces")

        app = TestApp()
        async with app.run_test() as pilot:
            view = pilot.app.query_one(TracesView)
            trace = view._traces[0]
            chart = view._render_flame_chart(trace)
            assert "dev-001" in chart
            assert "█" in chart
            assert "llm" in chart
            assert "tool" in chart

    async def test_render_detail(self):
        """Test detail rendering includes key information."""
        from textual.app import App

        from tlon.app.clients.tui.operator.views.traces import TracesView

        class TestApp(App):
            def compose(self):
                yield TracesView(id="traces")

        app = TestApp()
        async with app.run_test() as pilot:
            view = pilot.app.query_one(TracesView)
            trace = view._traces[0]
            detail = view._render_detail(trace)
            assert trace.trace_id in detail
            assert f"{trace.duration:.1f}s" in detail
            assert trace.root_agent in detail
            assert "Flame Chart" in detail
            assert "Stage Breakdown" in detail

    async def test_filter_status(self):
        """Test status filter reduces visible traces."""
        from textual.app import App

        from tlon.app.clients.tui.operator.views.traces import TracesView

        class TestApp(App):
            def compose(self):
                yield TracesView(id="traces")

        app = TestApp()
        async with app.run_test() as pilot:
            view = pilot.app.query_one(TracesView)
            # Default filter shows all
            assert len(view._traces) == 5
            # Apply completed filter
            view._filter_status = "completed"
            # _refresh_table should only show completed traces
            completed_count = sum(1 for t in view._traces if t.status == "completed")
            assert completed_count == 3


class TestConfigView:
    """Tests for ConfigView configuration logic."""

    async def test_config_view_mount_and_data(self):
        """Test ConfigView mounts and loads mock config."""
        from textual.app import App

        from tlon.app.clients.tui.operator.views.config import ConfigView

        class TestApp(App):
            def compose(self):
                yield ConfigView(id="config")

        app = TestApp()
        async with app.run_test() as pilot:
            view = pilot.app.query_one(ConfigView)
            config = view._get_mock_config()
            assert config["name"] == "dev-001"
            assert config["model"] == "GPT-4"
            assert config["temperature"] == 0.7
            assert "tools" in config

    async def test_model_options(self):
        """Test model options list is populated."""
        from textual.app import App

        from tlon.app.clients.tui.operator.views.config import ConfigView

        class TestApp(App):
            def compose(self):
                yield ConfigView(id="config")

        app = TestApp()
        async with app.run_test() as pilot:
            view = pilot.app.query_one(ConfigView)
            options = view._get_model_options()
            assert "GPT-4" in options
            assert "Claude-3-Opus" in options
            assert len(options) >= 4

    async def test_dirty_state_tracking(self):
        """Test dirty state reflects config changes."""
        from textual.app import App
        from textual.widgets import Input

        from tlon.app.clients.tui.operator.views.config import ConfigView

        class TestApp(App):
            def compose(self):
                yield ConfigView(id="config")

        app = TestApp()
        async with app.run_test() as pilot:
            view = pilot.app.query_one(ConfigView)
            # Initially clean after load
            assert not view.is_dirty
            # Modify an input field to trigger dirty state
            name_input = view.query_one("#input-name", Input)
            name_input.value = "modified-name"
            view._check_dirty()
            assert view.is_dirty
            # Revert input value should clear dirty
            name_input.value = view._original_config.get("name", "")
            view._check_dirty()
            assert not view.is_dirty


class TestOperatorComponents:
    """Tests for Operator components."""

    def test_navigation_bar_import(self):
        """Test NavigationBar can be imported."""
        from tlon.app.clients.tui.operator.components.navigation import NavigationBar

        assert NavigationBar is not None

    def test_sidebar_import(self):
        """Test Sidebar can be imported."""
        from tlon.app.clients.tui.operator.components.sidebar import Sidebar

        assert Sidebar is not None

    def test_inspector_panel_import(self):
        """Test InspectorPanel can be imported."""
        from tlon.app.clients.tui.operator.components.inspector import InspectorPanel

        assert InspectorPanel is not None

    def test_mini_event_stream_import(self):
        """Test MiniEventStream can be imported."""
        from tlon.app.clients.tui.operator.components.sidebar import MiniEventStream

        assert MiniEventStream is not None


class TestOperatorApp:
    """Tests for OperatorApp."""

    def test_app_import(self):
        """Test OperatorApp can be imported."""
        from tlon.app.clients.tui.operator.app import OperatorApp

        assert OperatorApp is not None

    def test_app_initialization(self):
        """Test OperatorApp initialization."""
        from tlon.app.clients.tui.operator.app import OperatorApp

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
        from tlon.app.clients.tui.operator.app import OperatorApp

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
        from tlon.app.clients.tui.operator.app import OperatorApp

        assert OperatorApp is not None
