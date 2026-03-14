"""Tests for AgentStore."""

import tempfile

from tlon.base.config import AgentConfig, LLMConfig, DockerRuntimeConfig
from tlon.app.wizard.agent_store import AgentStore


class TestAgentStore:
    """Test cases for AgentStore."""
    
    def test_save_and_load_agent(self):
        """Test saving and loading an agent."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = AgentStore(tmpdir)
            
            agent = AgentConfig(
                id="test-001",
                name="测试 Agent",
                personality="测试性格",
                position_id="developer",
                llm=LLMConfig(model_name="gpt-4", temperature=0.5),
                docker=DockerRuntimeConfig(image="ubuntu:22.04", memory_limit="1g"),
                skills=["file-operations"],
            )
            
            success, msg = store.save_agent(agent)
            assert success
            
            # Reload and check (must use force_reload=True since same store)
            loaded = store.get_agent("test-001")
            
            assert loaded is not None
            assert loaded.name == "测试 Agent"
            assert loaded.position_id == "developer"
            # LLMConfig reads from env, so we just check llm exists
            assert loaded.llm is not None
    
    def test_get_agent_by_name(self):
        """Test getting agent by name."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = AgentStore(tmpdir)
            
            agent = AgentConfig(
                id="test-002",
                name="Agent By Name",
                position_id="developer",
            )
            store.save_agent(agent)
            
            loaded = store.get_agent_by_name("Agent By Name")
            assert loaded is not None
            assert loaded.id == "test-002"
    
    def test_agent_exists(self):
        """Test checking if agent exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = AgentStore(tmpdir)
            
            agent = AgentConfig(
                id="existing",
                name="Existing Agent",
                position_id="developer",
            )
            store.save_agent(agent)
            
            assert store.agent_exists("existing")
            assert not store.agent_exists("nonexistent")
    
    def test_delete_agent(self):
        """Test deleting an agent."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with tempfile.TemporaryDirectory() as data_tmpdir:
                store = AgentStore(tmpdir, data_tmpdir)
                
                agent = AgentConfig(
                    id="deletable",
                    name="Deletable Agent",
                    position_id="developer",
                )
                store.save_agent(agent)
                assert store.agent_exists("deletable")
                
                # Delete
                success, msg = store.delete_agent("deletable")
                assert success
                assert not store.agent_exists("deletable")
    
    def test_get_agents_by_position(self):
        """Test getting agents by position."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = AgentStore(tmpdir)
            
            # Create agents with different positions
            agent1 = AgentConfig(id="dev-001", name="Dev 1", position_id="developer")
            agent2 = AgentConfig(id="dev-002", name="Dev 2", position_id="developer")
            agent3 = AgentConfig(id="ops-001", name="Ops 1", position_id="operator")
            
            store.save_agent(agent1)
            store.save_agent(agent2)
            store.save_agent(agent3)
            
            dev_agents = store.get_agents_by_position("developer")
            assert len(dev_agents) == 2
            
            ops_agents = store.get_agents_by_position("operator")
            assert len(ops_agents) == 1
    
    def test_generate_agent_id(self):
        """Test generating unique agent IDs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = AgentStore(tmpdir)
            
            existing = ["developer-001", "developer-002"]
            new_id = store.generate_agent_id("developer", existing)
            
            assert new_id == "developer-003"
            assert new_id not in existing
    
    def test_transfer_position(self):
        """Test transferring agent to new position."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = AgentStore(tmpdir)
            
            agent = AgentConfig(
                id="transferable",
                name="Transferable Agent",
                position_id="developer",
            )
            store.save_agent(agent)
            
            # Transfer
            success, msg = store.transfer_position("transferable", "operator")
            assert success
            
            # Verify
            loaded = store.get_agent("transferable")
            assert loaded.position_id == "operator"
    
    def test_validate_agent_data(self):
        """Test agent data validation."""
        store = AgentStore()
        
        # Valid data
        valid, errors = store.validate_agent_data({
            "id": "valid-id",
            "name": "Valid Name",
            "position_id": "developer",
        })
        assert valid
        assert len(errors) == 0
        
        # Missing required fields
        valid, errors = store.validate_agent_data({
            "id": "",
            "name": "",
        })
        assert not valid
        assert len(errors) > 0
