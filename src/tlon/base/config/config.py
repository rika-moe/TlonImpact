"""Configuration for Agents.

This module defines the Agent configuration class.
Positions are now managed separately in tlon.positions module.

Architecture:
    Position (岗位)          Agent (人员)
    ─────────────────        ─────────────────
    id: "developer"    ←──── position_id
    title: "开发工程师"       id: "dev-001"
    jd: "职责描述..."         name: "林知遥"
    proactive: false          personality: "细心"
                              skills: ["python"]
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional

from tlon.base.settings import LLMConfig


@dataclass
class DockerRuntimeConfig:
    """Docker runtime configuration."""
    
    image: str = "ubuntu:22.04"
    working_dir: str = "/workspace"
    memory_limit: str = "512m"
    cpu_limit: float = 1.0
    network_mode: str = "host"
    skills_dir: Optional[str] = None  # Path to skills directory to mount in container


@dataclass
class AgentConfig:
    """Complete agent (person) configuration.
    
    Each agent represents an individual person with their own personality,
    skills, and configuration. Agents are associated with a Position (job role)
    via position_id.
    
    Attributes:
        id: Unique agent identifier
        name: Display name (e.g., "林知遥")
        personality: Individual personality traits (个体性格特征)
        position_id: Reference to a Position type
        llm: LLM configuration
        docker: Docker runtime configuration
        memory: Memory configuration
        skills: List of skill names this agent has
        respond_to_all: If True, respond to all messages
    """
    id: str
    name: str
    personality: str = ""  # Individual traits, not position-based
    position_id: str = ""  # Reference to Position.type
    llm: Optional[LLMConfig] = None
    docker: Optional[DockerRuntimeConfig] = None
    memory: Any = field(default_factory=lambda: None)
    skills: List[str] = field(default_factory=list)
    respond_to_all: bool = False
    
    def __post_init__(self):
        """Validate configuration and set defaults."""
        # Set default memory config if not provided
        if self.memory is None:
            from tlon.subsystem.memory import MemoryConfig
            self.memory = MemoryConfig(agent_id=self.id)
        
        # Set default LLM config
        if self.llm is None:
            self.llm = LLMConfig()
        
        # Set default Docker config
        if self.docker is None:
            self.docker = DockerRuntimeConfig()
    
    def get_data_dir(self, base_dir: Path) -> Path:
        """Get the data directory for this agent.
        
        Args:
            base_dir: Base data directory
            
        Returns:
            Path to agent-specific data directory
        """
        agent_dir = base_dir / "agents" / self.id
        agent_dir.mkdir(parents=True, exist_ok=True)
        return agent_dir


__all__ = [
    "DockerRuntimeConfig",
    "AgentConfig",
]
