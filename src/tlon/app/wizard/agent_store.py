"""Agent data storage operations.

Handles loading, saving, and managing Agent configurations
from individual YAML files in config/agents/ directory.
"""

import shutil
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

from tlon.base.config.config import AgentConfig, DockerRuntimeConfig, LLMConfig
from tlon.base.logger import get_logger

logger = get_logger(__name__)


class AgentStore:
    """Store for managing Agent configurations.
    
    Handles loading agents from individual YAML files and provides
    CRUD operations with position reference management.
    """
    
    def __init__(self, agents_dir: str = "config/agents", data_dir: str = "data"):
        """Initialize the agent store.
        
        Args:
            agents_dir: Directory containing agent YAML files
            data_dir: Base directory for agent data storage
        """
        self.agents_dir = Path(agents_dir)
        self.data_dir = Path(data_dir)
        self._agents: Dict[str, AgentConfig] = {}
        self._ensure_directory()
    
    def _ensure_directory(self) -> None:
        """Ensure the agents directory exists."""
        self.agents_dir.mkdir(parents=True, exist_ok=True)
    
    def load_all_agents(self, force_reload: bool = False) -> Dict[str, AgentConfig]:
        """Load all agents from YAML files.
        
        Args:
            force_reload: Force reload from disk even if cached
            
        Returns:
            Dictionary mapping agent ID to AgentConfig object
        """
        if self._agents and not force_reload:
            return self._agents.copy()
        
        self._agents = {}
        
        if not self.agents_dir.exists():
            return self._agents.copy()
        
        yaml_files = list(self.agents_dir.glob("*.yaml"))
        
        for yaml_file in yaml_files:
            try:
                with open(yaml_file, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                
                if data and "agent" in data:
                    agent = self._dict_to_agent(data["agent"])
                    self._agents[agent.id] = agent
                    logger.debug(f"Loaded agent: {agent.id}")
            except Exception as e:
                logger.error(f"Failed to load agent from {yaml_file}: {e}")
        
        return self._agents.copy()
    
    def get_agent(self, agent_id: str) -> Optional[AgentConfig]:
        """Get an agent by ID.
        
        Args:
            agent_id: Agent identifier
            
        Returns:
            AgentConfig object or None if not found
        """
        if not self._agents:
            self.load_all_agents()
        return self._agents.get(agent_id)
    
    def get_agent_by_name(self, name: str) -> Optional[AgentConfig]:
        """Get an agent by name.
        
        Args:
            name: Agent display name
            
        Returns:
            AgentConfig object or None if not found
        """
        agents = self.load_all_agents()
        for agent in agents.values():
            if agent.name == name:
                return agent
        return None
    
    def save_agent(self, agent: AgentConfig) -> Tuple[bool, str]:
        """Save an agent to YAML file.
        
        Args:
            agent: AgentConfig to save
            
        Returns:
            Tuple of (success, message)
        """
        try:
            self._ensure_directory()
            
            # Convert agent to dict
            agent_dict = {
                "id": agent.id,
                "name": agent.name,
                "personality": agent.personality,
                "position_id": agent.position_id,
            }
            
            # Add LLM config if present
            if agent.llm:
                agent_dict["llm"] = {
                    "model_name": agent.llm.model_name,
                    "temperature": agent.llm.temperature,
                }
            
            # Add Docker config if present
            if agent.docker:
                agent_dict["docker"] = {
                    "image": agent.docker.image,
                    "memory_limit": agent.docker.memory_limit,
                }
            
            # Add skills
            agent_dict["skills"] = agent.skills
            
            data = {"agent": agent_dict}
            
            # Write to file
            filename = f"{agent.id}.yaml"
            filepath = self.agents_dir / filename
            
            with open(filepath, "w", encoding="utf-8") as f:
                yaml.dump(data, f, allow_unicode=True, sort_keys=False)
            
            # Update cache
            self._agents[agent.id] = agent
            
            # Ensure data directory exists
            agent_data_dir = self.data_dir / "agents" / agent.id
            agent_data_dir.mkdir(parents=True, exist_ok=True)
            
            logger.info(f"Saved agent: {agent.id} to {filepath}")
            return True, f"Agent saved to {filepath}"
            
        except Exception as e:
            logger.error(f"Failed to save agent {agent.id}: {e}")
            return False, f"Error: {e}"
    
    def delete_agent(self, agent_id: str, delete_data: bool = True) -> Tuple[bool, str]:
        """Delete an agent.
        
        Args:
            agent_id: Agent ID to delete
            delete_data: Also delete agent data directory
            
        Returns:
            Tuple of (success, message)
        """
        # Check if agent exists
        agent = self.get_agent(agent_id)
        if not agent:
            return False, f"Agent '{agent_id}' not found"
        
        try:
            # Delete config file
            filename = f"{agent_id}.yaml"
            filepath = self.agents_dir / filename
            
            if filepath.exists():
                filepath.unlink()
            
            # Remove from cache
            self._agents.pop(agent_id, None)
            
            # Delete data directory
            if delete_data:
                agent_data_dir = self.data_dir / "agents" / agent_id
                if agent_data_dir.exists():
                    shutil.rmtree(agent_data_dir)
                    logger.info(f"Deleted agent data directory: {agent_data_dir}")
            
            logger.info(f"Deleted agent: {agent_id}")
            return True, f"Agent '{agent_id}' deleted"
            
        except Exception as e:
            logger.error(f"Failed to delete agent {agent_id}: {e}")
            return False, f"Error: {e}"
    
    def get_agents_by_position(self, position_id: str) -> List[AgentConfig]:
        """Get all agents with a specific position.
        
        Args:
            position_id: Position type to filter by
            
        Returns:
            List of AgentConfig objects
        """
        agents = self.load_all_agents()
        return [agent for agent in agents.values() if agent.position_id == position_id]
    
    def agent_exists(self, agent_id: str) -> bool:
        """Check if an agent exists.
        
        Args:
            agent_id: Agent ID to check
            
        Returns:
            True if agent exists
        """
        return self.get_agent(agent_id) is not None
    
    def generate_agent_id(self, position_type: str, existing_ids: Optional[List[str]] = None) -> str:
        """Generate a unique agent ID.
        
        Args:
            position_type: Position type (used as prefix)
            existing_ids: List of existing IDs to avoid
            
        Returns:
            Unique agent ID
        """
        if existing_ids is None:
            existing_ids = list(self.load_all_agents().keys())
        
        # Normalize position type for ID
        prefix = position_type.replace("_", "-")
        
        # Find next available number
        counter = 1
        while True:
            agent_id = f"{prefix}-{counter:03d}"
            if agent_id not in existing_ids:
                return agent_id
            counter += 1
    
    def transfer_position(self, agent_id: str, new_position_id: str) -> Tuple[bool, str]:
        """Transfer an agent to a new position.
        
        Args:
            agent_id: Agent ID to transfer
            new_position_id: New position type
            
        Returns:
            Tuple of (success, message)
        """
        agent = self.get_agent(agent_id)
        if not agent:
            return False, f"Agent '{agent_id}' not found"
        
        old_position_id = agent.position_id
        agent.position_id = new_position_id
        
        success, msg = self.save_agent(agent)
        if success:
            logger.info(f"Transferred agent {agent_id} from {old_position_id} to {new_position_id}")
            return True, f"Agent transferred from '{old_position_id}' to '{new_position_id}'"
        else:
            # Revert on failure
            agent.position_id = old_position_id
            return False, msg
    
    def validate_agent_data(self, data: Dict) -> Tuple[bool, List[str]]:
        """Validate agent data.
        
        Args:
            data: Agent data dictionary
            
        Returns:
            Tuple of (is_valid, list_of_errors)
        """
        errors = []
        
        # Check required fields
        required_fields = ["id", "name", "position_id"]
        for field in required_fields:
            if field not in data or not data[field]:
                errors.append(f"Missing required field: {field}")
        
        # Validate ID format
        if "id" in data:
            agent_id = data["id"]
            if not agent_id.replace("-", "").replace("_", "").isalnum():
                errors.append("Agent ID must be alphanumeric with hyphens/underscores only")
        
        # Validate LLM config if present
        if "llm" in data and data["llm"]:
            llm = data["llm"]
            if "model_name" not in llm or not llm["model_name"]:
                errors.append("LLM config missing model_name")
        
        # Validate Docker config if present
        if "docker" in data and data["docker"]:
            docker = data["docker"]
            if "image" not in docker or not docker["image"]:
                errors.append("Docker config missing image")
        
        return len(errors) == 0, errors
    
    def _dict_to_agent(self, data: Dict) -> AgentConfig:
        """Convert dictionary to AgentConfig object.
        
        Args:
            data: Agent data dictionary
            
        Returns:
            AgentConfig object
        """
        # Parse LLM config
        llm = None
        if "llm" in data and data["llm"]:
            llm_data = data["llm"]
            llm = LLMConfig(
                model_name=llm_data.get("model_name", "kimi-k2.5"),
                temperature=llm_data.get("temperature", 0.7),
            )
        
        # Parse Docker config
        docker = None
        if "docker" in data and data["docker"]:
            docker_data = data["docker"]
            docker = DockerRuntimeConfig(
                image=docker_data.get("image", "ubuntu:22.04"),
                memory_limit=docker_data.get("memory_limit", "512m"),
            )
        
        return AgentConfig(
            id=data.get("id", ""),
            name=data.get("name", ""),
            personality=data.get("personality", ""),
            position_id=data.get("position_id", ""),
            llm=llm,
            docker=docker,
            skills=data.get("skills", []),
        )
