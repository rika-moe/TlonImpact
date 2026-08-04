"""Tests for AgentStore."""

import tempfile
from pathlib import Path

from tlon.app.wizard.agent_store import AgentStore
from tlon.base.config import AgentConfig, DockerRuntimeConfig, LLMConfig
from tlon.base.paths import TlonPaths


def _make_store(tmpdir: str) -> AgentStore:
    return AgentStore(paths=TlonPaths(home=Path(tmpdir)))


class TestAgentStore:
    """Test cases for AgentStore."""

    def test_save_and_load_agent(self):
        """Test saving and loading an agent."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = _make_store(tmpdir)

            agent = AgentConfig(
                id="test-001",
                name="测试 Agent",
                llm=LLMConfig(model_name="gpt-4", temperature=0.5),
                docker=DockerRuntimeConfig(image="ubuntu:22.04", memory_limit="1g"),
            )

            success, msg = store.save_agent(agent)
            assert success

            loaded = store.get_agent("test-001")
            assert loaded is not None
            assert loaded.name == "测试 Agent"
            assert loaded.llm is not None

    def test_get_agent_by_name(self):
        """Test getting agent by name."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = _make_store(tmpdir)

            agent = AgentConfig(
                id="test-002",
                name="Agent By Name",
            )
            store.save_agent(agent)

            loaded = store.get_agent_by_name("Agent By Name")
            assert loaded is not None
            assert loaded.id == "test-002"

    def test_agent_exists(self):
        """Test checking if agent exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = _make_store(tmpdir)

            agent = AgentConfig(
                id="existing",
                name="Existing Agent",
            )
            store.save_agent(agent)

            assert store.agent_exists("existing")
            assert not store.agent_exists("nonexistent")

    def test_delete_agent(self):
        """Test deleting an agent."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = _make_store(tmpdir)

            agent = AgentConfig(
                id="deletable",
                name="Deletable Agent",
            )
            store.save_agent(agent)
            assert store.agent_exists("deletable")

            success, msg = store.delete_agent("deletable")
            assert success
            assert not store.agent_exists("deletable")

    def test_generate_agent_id(self):
        """Test generating unique agent IDs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = _make_store(tmpdir)

            existing = ["developer-001", "developer-002"]
            new_id = store.generate_agent_id("developer", existing)

            assert new_id == "developer-003"
            assert new_id not in existing

    def test_validate_agent_data(self):
        """Test agent data validation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = _make_store(tmpdir)

            # Valid data
            valid, errors = store.validate_agent_data(
                {
                    "id": "valid-id",
                    "name": "Valid Name",
                }
            )
            assert valid
            assert len(errors) == 0

            # Missing required fields
            valid, errors = store.validate_agent_data(
                {
                    "id": "",
                    "name": "",
                }
            )
            assert not valid
            assert len(errors) > 0

    def test_write_behavior_file(self):
        """Written behavior.tlon parses as a complete, valid AgentSpec."""
        from tlon.agents.edn import loads
        from tlon.agents.spec import AgentSpec

        with tempfile.TemporaryDirectory() as tmpdir:
            store = _make_store(tmpdir)
            store.save_agent(AgentConfig(id="behaved", name="Behaved Agent"))

            store.write_behavior_file("behaved")

            behavior_file = TlonPaths(home=Path(tmpdir)).agent_behavior_file("behaved")
            assert behavior_file.exists()
            spec = AgentSpec.from_edn(loads(behavior_file.read_text(encoding="utf-8")))
            assert spec.version == 1
            assert spec.trigger.on_mention is True
