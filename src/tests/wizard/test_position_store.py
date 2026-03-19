"""Tests for PositionStore."""

import tempfile

from tlon.app.wizard.position_store import DEFAULT_POSITIONS, PositionStore
from tlon.organization.positions.models import Position


class TestPositionStore:
    """Test cases for PositionStore."""

    def test_load_default_templates(self):
        """Test loading default templates when no files exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = PositionStore(tmpdir)
            positions = store.load_all_positions()

            # Should load default templates
            assert len(positions) == len(DEFAULT_POSITIONS)
            assert "developer" in positions
            assert "operator" in positions

    def test_save_and_load_position(self):
        """Test saving and loading a position."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = PositionStore(tmpdir)

            position = Position(
                id="test_role",
                type="test_role",
                title="测试岗位",
                jd="这是一个测试岗位",
                department="测试部",
                rules=["规则1", "规则2"],
                default_skills=["skill1"],
            )

            success, msg = store.save_position(position)
            assert success

            # Reload and check
            store2 = PositionStore(tmpdir)
            positions = store2.load_all_positions()

            assert "test_role" in positions
            assert positions["test_role"].title == "测试岗位"

    def test_get_position(self):
        """Test getting a specific position."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = PositionStore(tmpdir)

            # Should return default template
            pos = store.get_position("developer")
            assert pos is not None
            assert pos.type == "developer"

            # Non-existent position
            pos = store.get_position("nonexistent")
            assert pos is None

    def test_position_exists(self):
        """Test checking if position exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = PositionStore(tmpdir)

            assert store.position_exists("developer")
            assert not store.position_exists("nonexistent")

    def test_delete_position(self):
        """Test deleting a position."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = PositionStore(tmpdir)

            # Create a position first
            position = Position(
                id="deletable",
                type="deletable",
                title="可删除岗位",
                jd="描述",
            )
            store.save_position(position)

            # Delete it
            success, msg = store.delete_position("deletable")
            assert success
            assert not store.position_exists("deletable")

    def test_delete_position_with_references(self):
        """Test that deleting a referenced position fails."""
        with tempfile.TemporaryDirectory() as tmpdir, tempfile.TemporaryDirectory() as agent_tmpdir:
            from tlon.app.wizard.agent_store import AgentStore
            from tlon.base.config import AgentConfig

            pos_store = PositionStore(tmpdir)
            agent_store = AgentStore(agent_tmpdir)

            # Create position
            position = Position(
                id="referenced",
                type="referenced",
                title="被引用的岗位",
                jd="描述",
            )
            pos_store.save_position(position)

            # Create agent referencing it
            agent = AgentConfig(
                id="test-agent",
                name="Test Agent",
                position_id="referenced",
            )
            agent_store.save_agent(agent)

            # Try to delete - should fail
            success, msg = pos_store.delete_position("referenced", agent_store)
            assert not success
            assert "正在使用" in msg or "using" in msg

    def test_create_from_template(self):
        """Test creating position from template."""
        store = PositionStore()

        position = store.create_from_template(
            "developer",
            {
                "type": "custom_dev",
                "title": "自定义开发",
            },
        )

        assert position is not None
        assert position.type == "custom_dev"
        assert position.title == "自定义开发"
        assert len(position.rules) > 0  # Should inherit rules from template

    def test_validate_position_data(self):
        """Test position data validation."""
        store = PositionStore()

        # Valid data (note: validate_position_data expects 'type', not 'id')
        valid, errors = store.validate_position_data(
            {
                "type": "valid_type",
                "title": "Valid Title",
                "jd": "Valid description",
            }
        )
        assert valid
        assert len(errors) == 0

        # Missing required fields
        valid, errors = store.validate_position_data(
            {
                "type": "",
                "title": "",
            }
        )
        assert not valid
        assert len(errors) > 0

    def test_get_default_positions(self):
        """Test getting default positions."""
        store = PositionStore()
        templates = store.get_default_positions()

        assert len(templates) > 0
        assert "developer" in templates
        assert "operator" in templates
