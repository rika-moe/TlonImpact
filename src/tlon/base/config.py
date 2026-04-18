"""Configuration system for Tlon using Pydantic.

Unified configuration management with validation and EventBus integration.
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

from tlon.base.component import Component
from tlon.base.device import Device
from tlon.base.events.event import EventType
from tlon.base.events.event_bus import EventBus
from tlon.base.logger import get_logger
from tlon.base.settings import get_default_config_dir, get_default_data_dir

logger = get_logger(__name__)


# =============================================================================
# Abstract Interface
# =============================================================================


class AgentInfoProvider(ABC):
    """Abstract interface for agent configuration queries."""

    @abstractmethod
    def get_config(self, agent_id: str) -> "AgentConfig | None":
        """Get agent configuration by ID."""
        pass

    @abstractmethod
    def get_all_ids(self) -> set[str]:
        """Get all agent IDs."""
        pass

    @abstractmethod
    def is_proactive(self, agent_id: str) -> bool:
        """Check if an agent is proactive."""
        pass


# =============================================================================
# Prompt Template Models
# =============================================================================


# =============================================================================
# Agent Configuration
# =============================================================================


class LLMConfig(BaseModel):
    """LLM configuration for an agent."""

    model_name: str = "kimi-k2.5"
    temperature: float = 1.0
    max_tokens: int = 4096
    top_p: float = 0.9

    # Runtime-only fields (not persisted to YAML)
    base_url: str = Field(default="", exclude=True)
    auth_token: str = Field(default="", exclude=True)
    max_retries: int = Field(default=3, exclude=True)
    base_delay: float = Field(default=1.0, exclude=True)

    @field_validator("temperature")
    @classmethod
    def validate_temperature(cls, v: float) -> float:
        if not 0 <= v <= 2:
            raise ValueError("temperature must be between 0 and 2")
        return v


class DockerRuntimeConfig(BaseModel):
    """Docker runtime configuration."""

    image: str = "ubuntu:22.04"
    working_dir: str = "/workspace"
    memory_limit: str = "512m"
    cpu_limit: float = 1.0
    network_mode: str = "host"


class AgentConfig(BaseModel):
    """Complete agent configuration."""

    id: str
    name: str

    # Prompt narratives (free text, not structured fields)
    identity: str = ""
    profile: str = ""
    behavior: str = ""

    llm: LLMConfig = Field(default_factory=LLMConfig)
    docker: DockerRuntimeConfig = Field(default_factory=DockerRuntimeConfig)
    memory: Any = None
    proactive: bool = False

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        if not v:
            raise ValueError("agent id is required")
        if not v.replace("-", "").replace("_", "").isalnum():
            raise ValueError("agent ID must be alphanumeric with hyphens/underscores only")
        return v

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not v:
            raise ValueError("agent name is required")
        return v

    def get_data_dir(self, base_dir: Path) -> Path:
        """Get the data directory for this agent."""
        agent_dir = base_dir / "agents" / self.id
        agent_dir.mkdir(parents=True, exist_ok=True)
        return agent_dir


# =============================================================================
# Config Service - Unified Component + Device
# =============================================================================


class ConfigService(Component, Device, AgentInfoProvider):
    """Unified configuration service with loading, caching, and EventBus notifications.

    Responsibilities:
    - Load configuration from YAML files
    - Maintain cached configuration state
    - Notify subscribers of configuration changes via EventBus
    - Save configuration changes to files
    """

    def __init__(
        self,
        event_bus: EventBus,
        config_dir: Path | None = None,
        data_dir: Path | None = None,
    ):
        super().__init__(name="ConfigService")
        self._event_bus = event_bus
        self._config_dir = config_dir or get_default_config_dir()
        self._agents_dir = self._config_dir / "agents"
        self._data_dir = data_dir or get_default_data_dir()
        self._agents: dict[str, AgentConfig] = {}
        logger.info("ConfigService constructed")

    async def initialize(self) -> None:
        """Initialize by loading all configurations."""
        logger.info("Loading initial configuration...")
        self._load_all_agents()
        self._initialized = True
        logger.info(f"Loaded {len(self._agents)} agents")

    async def shutdown(self) -> None:
        """Shutdown the configuration service."""
        self.unsubscribe_all()
        self._initialized = False
        logger.info("ConfigService shutdown")

    def _load_all_agents(self) -> None:
        """Load all agents from YAML files."""
        self._agents = {}
        if not self._agents_dir.exists():
            return

        for yaml_file in self._agents_dir.glob("*.yaml"):
            try:
                agent = self._load_agent_file(yaml_file)
                if agent:
                    self._agents[agent.id] = agent
            except Exception as e:
                logger.warning(f"Failed to load agent from {yaml_file}: {e}")

    def _load_agent_file(self, filepath: Path) -> AgentConfig | None:
        """Load a single agent from YAML file."""
        data = yaml.safe_load(filepath.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "agent" not in data:
            raise ValueError("Invalid structure: missing 'agent' key")
        return AgentConfig.model_validate(data["agent"])

    def get_agent(self, agent_id: str) -> AgentConfig | None:
        """Get a specific agent by ID."""
        return self._agents.get(agent_id)

    def get_agent_by_name(self, name: str) -> AgentConfig | None:
        """Get an agent by display name."""
        for agent in self._agents.values():
            if agent.name == name:
                return agent
        return None

    @property
    def agents(self) -> dict[str, AgentConfig]:
        """Get current agent configurations (returns copy)."""
        return self._agents.copy()

    def get_all_ids(self) -> set[str]:
        """Get all agent IDs (AgentInfoProvider interface)."""
        return set(self._agents.keys())

    def is_proactive(self, agent_id: str) -> bool:
        """Check if an agent is proactive (AgentInfoProvider interface)."""
        config = self._agents.get(agent_id)
        if config:
            return config.proactive
        return False

    # AgentInfoProvider interface alias
    def get_config(self, agent_id: str) -> AgentConfig | None:
        """Get agent configuration by ID (AgentInfoProvider interface)."""
        return self._agents.get(agent_id)

    async def reload_agent(self, agent_id: str) -> bool:
        """Reload a specific agent from file and emit update event."""
        filepath = self._agents_dir / f"{agent_id}.yaml"
        if not filepath.exists():
            return False

        try:
            agent = self._load_agent_file(filepath)
            if agent:
                self._agents[agent_id] = agent
                await self.emit(
                    EventType.CONFIG_UPDATED,
                    {"agent_id": agent_id, "action": "reload"},
                )
                return True
        except Exception as e:
            logger.error(f"Failed to reload agent {agent_id}: {e}")

        return False

    def save_agent(self, agent: AgentConfig, create_data_dir: bool = True) -> None:
        """Save an agent to YAML file.

        Note: Updates memory cache first, then writes to file for atomicity.
        If file write fails, memory will be consistent on next reload.
        """
        self._agents_dir.mkdir(parents=True, exist_ok=True)

        # Step 1: Update memory first (idempotent, can be retried)
        self._agents[agent.id] = agent

        # Step 2: Create data directory
        if create_data_dir:
            agent.get_data_dir(self._data_dir)

        # Step 3: Write to file (with rollback on failure)
        data = {"agent": agent.model_dump(exclude_none=True)}
        filepath = self._agents_dir / f"{agent.id}.yaml"
        try:
            filepath.write_text(
                yaml.dump(
                    data, allow_unicode=True, sort_keys=False, default_flow_style=False, indent=2
                ),
                encoding="utf-8",
            )
            logger.info(f"Agent saved to {filepath}")
        except Exception:
            # Rollback memory on file write failure
            self._agents.pop(agent.id, None)
            raise

    def delete_agent(self, agent_id: str, delete_data: bool = True) -> None:
        """Delete an agent."""
        import shutil

        filepath = self._agents_dir / f"{agent_id}.yaml"
        if not filepath.exists():
            raise ValueError(f"Agent '{agent_id}' not found")

        filepath.unlink()

        if delete_data:
            agent_data_dir = self._data_dir / "agents" / agent_id
            if agent_data_dir.exists():
                shutil.rmtree(agent_data_dir)

        self._agents.pop(agent_id, None)
        logger.info(f"Agent '{agent_id}' deleted")


__all__ = [
    "AgentInfoProvider",
    "LLMConfig",
    "DockerRuntimeConfig",
    "AgentConfig",
    "ConfigService",
]
