"""Tests for config loader."""

import tempfile
from pathlib import Path

import yaml

from tlon.base.config.loader import ConfigLoader


class TestConfigLoaderInit:
    """Test ConfigLoader initialization."""

    def test_default_init(self):
        """Test default initialization."""
        loader = ConfigLoader()
        assert loader.config_dir.name == "config"
        assert loader.agents_dir.name == "agents"

    def test_custom_agents_dir(self):
        """Test initialization with custom agents directory."""
        loader = ConfigLoader(agents_dir="/custom/agents")
        assert str(loader.agents_dir) == "/custom/agents"


class TestLoadAgents:
    """Test agent loading."""

    def test_load_all_agents_empty_dir(self):
        """Test loading from empty directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            loader = ConfigLoader(agents_dir=tmpdir)
            agents = loader.load_all_agents()
            assert len(agents) == 0

    def test_load_single_agent(self):
        """Test loading a single agent."""
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_data = {
                "agent": {
                    "id": "dev-001",
                    "name": "林知遥",
                }
            }
            with open(Path(tmpdir) / "dev-001.yaml", "w") as f:
                yaml.dump(agent_data, f)

            loader = ConfigLoader(agents_dir=tmpdir)
            agents = loader.load_all_agents()

            assert len(agents) == 1
            assert "dev-001" in agents
            assert agents["dev-001"].name == "林知遥"

    def test_load_agent_by_id(self):
        """Test loading a specific agent by ID."""
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_data = {
                "agent": {
                    "id": "dev-001",
                    "name": "林知遥",
                }
            }
            with open(Path(tmpdir) / "dev-001.yaml", "w") as f:
                yaml.dump(agent_data, f)

            loader = ConfigLoader(agents_dir=tmpdir)
            agent = loader.load_agent("dev-001")

            assert agent is not None
            assert agent.name == "林知遥"

    def test_load_agent_by_name(self):
        """Test loading an agent by name."""
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_data = {
                "agent": {
                    "id": "dev-001",
                    "name": "林知遥",
                }
            }
            with open(Path(tmpdir) / "dev-001.yaml", "w") as f:
                yaml.dump(agent_data, f)

            loader = ConfigLoader(agents_dir=tmpdir)
            agent = loader.load_agent_by_name("林知遥")

            assert agent is not None
            assert agent.id == "dev-001"


class TestLoadPromptTemplates:
    """Test prompt templates loading."""

    def test_load_default_templates(self):
        """Test loading default templates when file doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            loader = ConfigLoader(config_dir=tmpdir)
            templates = loader.load_prompt_templates()

            assert templates is not None
            assert len(templates.workflow) > 0
            assert len(templates.important_notes) > 0
            assert len(templates.discipline_rules) > 0

    def test_load_custom_templates(self):
        """Test loading custom templates from file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            templates_data = {
                "templates": {
                    "workflow": "custom workflow",
                    "important_notes": "custom notes",
                    "discipline_rules": "custom rules",
                }
            }
            with open(Path(tmpdir) / "prompt_templates.yaml", "w") as f:
                yaml.dump(templates_data, f)

            loader = ConfigLoader(config_dir=tmpdir)
            templates = loader.load_prompt_templates()

            assert templates.workflow == "custom workflow"
            assert templates.important_notes == "custom notes"
            assert templates.discipline_rules == "custom rules"


class TestClearCache:
    """Test cache clearing."""

    def test_clear_cache(self):
        """Test clearing all caches."""
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_data = {
                "agent": {
                    "id": "dev-001",
                    "name": "林知遥",
                }
            }
            with open(Path(tmpdir) / "dev-001.yaml", "w") as f:
                yaml.dump(agent_data, f)

            loader = ConfigLoader(agents_dir=tmpdir)
            loader.load_all_agents()

            assert loader._agents is not None

            loader.clear_cache()

            assert loader._agents is None
