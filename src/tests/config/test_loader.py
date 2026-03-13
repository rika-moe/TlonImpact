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
        assert loader.positions_dir.name == "positions"
        assert loader.agents_dir.name == "agents"
    
    def test_custom_dirs(self):
        """Test initialization with custom directories."""
        loader = ConfigLoader(
            config_dir="/custom/config",
            positions_dir="/custom/positions",
            agents_dir="/custom/agents",
        )
        assert str(loader.config_dir) == "/custom/config"
        assert str(loader.positions_dir) == "/custom/positions"
        assert str(loader.agents_dir) == "/custom/agents"


class TestLoadPositions:
    """Test position loading."""
    
    def test_load_all_positions_empty_dir(self):
        """Test loading from empty directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            loader = ConfigLoader(positions_dir=tmpdir)
            positions = loader.load_all_positions()
            assert len(positions) == 0
    
    def test_load_single_position(self):
        """Test loading a single position."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a position file
            position_data = {
                "position": {
                    "type": "developer",
                    "title": "开发工程师",
                    "jd": "负责开发",
                }
            }
            with open(Path(tmpdir) / "developer.yaml", "w") as f:
                yaml.dump(position_data, f)
            
            loader = ConfigLoader(positions_dir=tmpdir)
            positions = loader.load_all_positions()
            
            assert len(positions) == 1
            assert "developer" in positions
            assert positions["developer"].title == "开发工程师"
    
    def test_load_position_by_type(self):
        """Test loading a specific position by type."""
        with tempfile.TemporaryDirectory() as tmpdir:
            position_data = {
                "position": {
                    "type": "developer",
                    "title": "开发工程师",
                    "jd": "负责开发",
                }
            }
            with open(Path(tmpdir) / "developer.yaml", "w") as f:
                yaml.dump(position_data, f)
            
            loader = ConfigLoader(positions_dir=tmpdir)
            position = loader.load_position("developer")
            
            assert position is not None
            assert position.title == "开发工程师"
    
    def test_load_nonexistent_position(self):
        """Test loading a non-existent position."""
        with tempfile.TemporaryDirectory() as tmpdir:
            loader = ConfigLoader(positions_dir=tmpdir)
            position = loader.load_position("nonexistent")
            assert position is None
    
    def test_load_position_uses_cache(self):
        """Test that cached positions are returned."""
        with tempfile.TemporaryDirectory() as tmpdir:
            position_data = {
                "position": {
                    "type": "developer",
                    "title": "开发工程师",
                    "jd": "负责开发",
                }
            }
            with open(Path(tmpdir) / "developer.yaml", "w") as f:
                yaml.dump(position_data, f)
            
            loader = ConfigLoader(positions_dir=tmpdir)
            
            # First load
            positions1 = loader.load_all_positions()
            
            # Second load should use cache
            positions2 = loader.load_all_positions()
            
            assert positions1 == positions2
    
    def test_load_position_force_reload(self):
        """Test force reload bypasses cache."""
        with tempfile.TemporaryDirectory() as tmpdir:
            position_data = {
                "position": {
                    "type": "developer",
                    "title": "开发工程师",
                    "jd": "负责开发",
                }
            }
            with open(Path(tmpdir) / "developer.yaml", "w") as f:
                yaml.dump(position_data, f)
            
            loader = ConfigLoader(positions_dir=tmpdir)
            loader.load_all_positions()
            
            # Modify file
            position_data["position"]["title"] = "高级开发"
            with open(Path(tmpdir) / "developer.yaml", "w") as f:
                yaml.dump(position_data, f)
            
            # Without force_reload, should get old value
            cached = loader.load_all_positions()
            assert cached["developer"].title == "开发工程师"
            
            # With force_reload, should get new value
            loader.clear_cache()
            reloaded = loader.load_all_positions()
            assert reloaded["developer"].title == "高级开发"


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
                    "position_id": "developer",
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
                    "position_id": "developer",
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
                    "position_id": "developer",
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


class TestGetAgentsByPosition:
    """Test getting agents by position."""
    
    def test_get_agents_by_position(self):
        """Test filtering agents by position."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create agents with different positions
            agent1 = {"agent": {"id": "dev-001", "name": "Dev 1", "position_id": "developer"}}
            agent2 = {"agent": {"id": "dev-002", "name": "Dev 2", "position_id": "developer"}}
            agent3 = {"agent": {"id": "pm-001", "name": "PM 1", "position_id": "project_manager"}}
            
            for agent_data in [agent1, agent2, agent3]:
                with open(Path(tmpdir) / f"{agent_data['agent']['id']}.yaml", "w") as f:
                    yaml.dump(agent_data, f)
            
            loader = ConfigLoader(agents_dir=tmpdir)
            dev_agents = loader.get_agents_by_position("developer")
            
            assert len(dev_agents) == 2
            assert "dev-001" in dev_agents
            assert "dev-002" in dev_agents


class TestClearCache:
    """Test cache clearing."""
    
    def test_clear_cache(self):
        """Test clearing all caches."""
        with tempfile.TemporaryDirectory() as tmpdir:
            position_data = {
                "position": {
                    "type": "developer",
                    "title": "开发工程师",
                    "jd": "负责开发",
                }
            }
            with open(Path(tmpdir) / "developer.yaml", "w") as f:
                yaml.dump(position_data, f)
            
            loader = ConfigLoader(positions_dir=tmpdir)
            loader.load_all_positions()
            
            assert loader._positions is not None
            
            loader.clear_cache()
            
            assert loader._positions is None
