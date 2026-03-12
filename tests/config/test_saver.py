"""Tests for config saver."""

import tempfile
from pathlib import Path

import pytest
import yaml

from tlon.config import ConfigSaver, PositionConfig, AgentConfig


class TestConfigSaverInit:
    """Test ConfigSaver initialization."""
    
    def test_default_init(self):
        """Test default initialization."""
        saver = ConfigSaver()
        assert saver.config_dir.name == "config"
        assert saver.positions_dir.name == "positions"
        assert saver.agents_dir.name == "agents"
        assert saver.data_dir.name == "data"
    
    def test_custom_dirs(self):
        """Test initialization with custom directories."""
        saver = ConfigSaver(
            config_dir="/custom/config",
            positions_dir="/custom/positions",
            agents_dir="/custom/agents",
            data_dir="/custom/data",
        )
        assert str(saver.config_dir) == "/custom/config"
        assert str(saver.positions_dir) == "/custom/positions"
        assert str(saver.agents_dir) == "/custom/agents"
        assert str(saver.data_dir) == "/custom/data"


class TestSavePosition:
    """Test position saving."""
    
    def test_save_valid_position(self):
        """Test saving a valid position."""
        with tempfile.TemporaryDirectory() as tmpdir:
            saver = ConfigSaver(positions_dir=tmpdir)
            position = PositionConfig(
                id="developer",
                type="developer",
                title="开发工程师",
                jd="负责开发",
            )
            
            success, message = saver.save_position(position)
            
            assert success is True
            assert "developer.yaml" in message
            assert (Path(tmpdir) / "developer.yaml").exists()
    
    def test_save_invalid_position(self):
        """Test saving an invalid position fails."""
        with tempfile.TemporaryDirectory() as tmpdir:
            saver = ConfigSaver(positions_dir=tmpdir)
            position = PositionConfig(
                id="",
                type="developer",
                title="",
                jd="",
            )
            
            success, message = saver.save_position(position)
            
            assert success is False
            assert "Validation failed" in message


class TestDeletePosition:
    """Test position deletion."""
    
    def test_delete_existing_position(self):
        """Test deleting an existing position."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a position file
            position_file = Path(tmpdir) / "developer.yaml"
            position_file.write_text("position:\n  type: developer")
            
            saver = ConfigSaver(positions_dir=tmpdir)
            success, message = saver.delete_position("developer")
            
            assert success is True
            assert "deleted" in message
            assert not position_file.exists()
    
    def test_delete_nonexistent_position(self):
        """Test deleting a non-existent position."""
        with tempfile.TemporaryDirectory() as tmpdir:
            saver = ConfigSaver(positions_dir=tmpdir)
            success, message = saver.delete_position("nonexistent")
            
            assert success is False
            assert "not found" in message
    
    def test_delete_position_with_references(self):
        """Test deleting a position that's in use."""
        with tempfile.TemporaryDirectory() as tmpdir:
            saver = ConfigSaver(positions_dir=tmpdir)
            success, message = saver.delete_position("developer", agent_ids=["agent-001"])
            
            assert success is False
            assert "Cannot delete" in message


class TestSaveAgent:
    """Test agent saving."""
    
    def test_save_valid_agent(self):
        """Test saving a valid agent."""
        with tempfile.TemporaryDirectory() as tmpdir:
            saver = ConfigSaver(agents_dir=tmpdir, data_dir=tmpdir)
            agent = AgentConfig(
                id="dev-001",
                name="林知遥",
                position_id="developer",
            )
            
            success, message = saver.save_agent(agent)
            
            assert success is True
            assert "dev-001.yaml" in message
            assert (Path(tmpdir) / "dev-001.yaml").exists()
    
    def test_save_invalid_agent(self):
        """Test saving an invalid agent fails."""
        with tempfile.TemporaryDirectory() as tmpdir:
            saver = ConfigSaver(agents_dir=tmpdir, data_dir=tmpdir)
            agent = AgentConfig(
                id="",
                name="",
                position_id="",
            )
            
            success, message = saver.save_agent(agent)
            
            assert success is False
            assert "Validation failed" in message
    
    def test_save_agent_creates_data_dir(self):
        """Test saving agent creates data directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            saver = ConfigSaver(agents_dir=tmpdir, data_dir=tmpdir)
            agent = AgentConfig(
                id="dev-001",
                name="林知遥",
                position_id="developer",
            )
            
            saver.save_agent(agent, create_data_dir=True)
            
            assert (Path(tmpdir) / "agents" / "dev-001").exists()


class TestDeleteAgent:
    """Test agent deletion."""
    
    def test_delete_existing_agent(self):
        """Test deleting an existing agent."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create an agent file
            agent_file = Path(tmpdir) / "dev-001.yaml"
            agent_file.write_text("agent:\n  id: dev-001")
            
            saver = ConfigSaver(agents_dir=tmpdir)
            success, message = saver.delete_agent("dev-001", delete_data=False)
            
            assert success is True
            assert "deleted" in message
            assert not agent_file.exists()
    
    def test_delete_agent_with_data(self):
        """Test deleting agent and its data directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            agents_dir = Path(tmpdir) / "agents_config"
            agents_dir.mkdir()
            
            agent_file = agents_dir / "dev-001.yaml"
            agent_file.write_text("agent:\n  id: dev-001")
            
            data_dir = Path(tmpdir) / "agents" / "dev-001"
            data_dir.mkdir(parents=True)
            (data_dir / "test.txt").write_text("test")
            
            saver = ConfigSaver(agents_dir=str(agents_dir), data_dir=tmpdir)
            success, message = saver.delete_agent("dev-001", delete_data=True)
            
            assert success is True
            assert not data_dir.exists()


class TestTransferAgentPosition:
    """Test agent position transfer."""
    
    def test_transfer_position(self):
        """Test transferring agent to new position."""
        with tempfile.TemporaryDirectory() as tmpdir:
            saver = ConfigSaver(agents_dir=tmpdir, data_dir=tmpdir)
            agent = AgentConfig(
                id="dev-001",
                name="林知遥",
                position_id="developer",
            )
            
            success, message = saver.transfer_agent_position(agent, "project_manager")
            
            assert success is True
            assert "project_manager" in message
            assert agent.position_id == "project_manager"


class TestGenerateAgentId:
    """Test agent ID generation."""
    
    def test_generate_first_agent_id(self):
        """Test generating first agent ID."""
        saver = ConfigSaver()
        agent_id = saver.generate_agent_id("developer")
        assert agent_id == "developer-001"
    
    def test_generate_avoids_existing_ids(self):
        """Test generating ID avoids existing IDs."""
        saver = ConfigSaver()
        existing = ["developer-001", "developer-002"]
        agent_id = saver.generate_agent_id("developer", existing)
        assert agent_id == "developer-003"
    
    def test_generate_underscore_to_hyphen(self):
        """Test underscore in position type is converted to hyphen."""
        saver = ConfigSaver()
        agent_id = saver.generate_agent_id("project_manager")
        assert agent_id == "project-manager-001"


class TestSavePromptTemplates:
    """Test prompt templates saving."""
    
    def test_save_valid_templates(self):
        """Test saving valid prompt templates."""
        with tempfile.TemporaryDirectory() as tmpdir:
            saver = ConfigSaver(config_dir=tmpdir)
            from tlon.config import PromptTemplates
            templates = PromptTemplates(
                workflow="custom workflow",
                important_notes="custom notes",
                discipline_rules="custom rules",
            )
            
            success, message = saver.save_prompt_templates(templates)
            
            assert success is True
            assert "prompt_templates.yaml" in message
            assert (Path(tmpdir) / "prompt_templates.yaml").exists()
    
    def test_save_invalid_templates(self):
        """Test saving invalid templates fails."""
        with tempfile.TemporaryDirectory() as tmpdir:
            saver = ConfigSaver(config_dir=tmpdir)
            from tlon.config import PromptTemplates
            # Create templates with invalid field type
            templates = PromptTemplates()
            templates.workflow = 123  # Invalid type
            
            success, message = saver.save_prompt_templates(templates)
            
            assert success is False
            assert "Validation failed" in message
