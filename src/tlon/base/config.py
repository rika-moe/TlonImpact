"""Configuration system for Tlon using Pydantic.

Unified configuration management with validation and EventBus integration.
"""

from abc import ABC, abstractmethod
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator

from tlon.base.component import Component
from tlon.base.device import Device
from tlon.base.events.event import EventType
from tlon.base.events.event_bus import EventBus
from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths, paths
from tlon.base.settings import get_agent_home, get_tlon_home

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
    request_timeout: float = Field(default=300.0, exclude=True)

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


class MemoryConfig(BaseModel):
    """Memory configuration for an agent."""

    max_history: int = 10
    summary_window: int = 20


class ToolConfig(BaseModel):
    """Tool execution configuration for an agent."""

    total_timeout: float = 60.0
    per_timeout: float = 30.0
    # Filesystem tools (read/write/edit) may only touch paths under these
    # absolute container directories.
    allowed_paths: list[str] = Field(default_factory=lambda: ["/workspace"])

    @field_validator("allowed_paths")
    @classmethod
    def validate_allowed_paths(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("allowed_paths must not be empty")
        for p in v:
            if not p.startswith("/"):
                raise ValueError(f"allowed_paths entries must be absolute container paths: {p!r}")
        return v


class AgentConfig(BaseModel):
    """Complete agent configuration.

    Narrative identity/personality/rules are stored as .md files
    (IDENTITY.md, SOUL.md, RULES.md) in the agent's home directory.
    """

    id: str
    name: str

    llm: LLMConfig = Field(default_factory=LLMConfig)
    docker: DockerRuntimeConfig = Field(default_factory=DockerRuntimeConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    tool: ToolConfig = Field(default_factory=ToolConfig)
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
        return get_agent_home(self.id)


# =============================================================================
# Standalone YAML CRUD operations (shared by ConfigService and Wizard)
# =============================================================================


def _yaml_load_file(filepath: Path) -> AgentConfig | None:
    """Load a single agent from a YAML file."""
    try:
        data = yaml.safe_load(filepath.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "agent" not in data:
            return None
        return AgentConfig.model_validate(data["agent"])
    except Exception:
        return None


def _yaml_load_all(tlon_home: Path | None = None) -> dict[str, AgentConfig]:
    """Load all agents by scanning ~/.tlon/*/config/agent.yaml.

    Skips the 'shared' directory and non-directory entries.
    """
    p = TlonPaths(home=tlon_home) if tlon_home else paths
    home = p.tlon_home()
    agents: dict[str, AgentConfig] = {}
    if not home.exists():
        return agents
    for agent_dir in sorted(home.iterdir()):
        if not agent_dir.is_dir() or agent_dir.name == "shared":
            continue
        yaml_file = p.agent_config_file(agent_dir.name)
        if yaml_file.exists():
            agent = _yaml_load_file(yaml_file)
            if agent:
                agents[agent.id] = agent
    return agents


def _yaml_save(agent: AgentConfig, tlon_home: Path | None = None) -> None:
    """Save an agent to its per-agent config directory.

    Writes ~/.tlon/{agent.id}/config/agent.yaml and ensures
    standard subdirectories (workspace, memory) exist.
    """
    p = TlonPaths(home=tlon_home) if tlon_home else paths
    yaml_file = p.agent_config_file(agent.id)
    yaml_file.parent.mkdir(parents=True, exist_ok=True)
    data = {"agent": agent.model_dump(exclude_none=True)}
    yaml_file.write_text(
        yaml.dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False, indent=2),
        encoding="utf-8",
    )
    p.agent_workspace_dir(agent.id)
    p.agent_memory_dir(agent.id)


def _yaml_delete(agent_id: str, tlon_home: Path | None = None) -> None:
    """Delete an agent entirely — removes ~/.tlon/{agent_id}/ directory."""
    import shutil

    p = TlonPaths(home=tlon_home) if tlon_home else paths
    agent_home = p.agent_dir(agent_id)
    if not agent_home.exists():
        raise ValueError(f"Agent '{agent_id}' not found")
    shutil.rmtree(agent_home)


def _yaml_exists(agent_id: str, tlon_home: Path | None = None) -> bool:
    """Check if an agent config exists at ~/.tlon/{agent_id}/config/agent.yaml."""
    p = TlonPaths(home=tlon_home) if tlon_home else paths
    return p.agent_config_file(agent_id).exists()


# =============================================================================
# Config Service - Unified Component + Device
# =============================================================================


class ConfigService(Component, Device, AgentInfoProvider):
    """Unified configuration service with loading, caching, and EventBus notifications.

    Responsibilities:
    - Load configuration from per-agent YAML files (~/.tlon/{id}/config/agent.yaml)
    - Maintain cached configuration state
    - Notify subscribers of configuration changes via EventBus
    - Save configuration changes to files
    """

    def __init__(
        self,
        event_bus: EventBus,
        tlon_home: Path | None = None,
    ):
        # Initialize both parents (MRO super() would skip Device.__init__)
        Component.__init__(self, name="ConfigService")
        Device.__init__(self, event_bus=event_bus, name="ConfigService")
        self._tlon_home = tlon_home or get_tlon_home()
        self._agents: dict[str, AgentConfig] = {}
        logger.info("config_service_constructed")

    async def initialize(self) -> None:
        """Initialize by loading all configurations."""
        logger.info("config_load_started")
        self._load_all_agents()
        self._initialized = True
        logger.info("agents_loaded", agent_count=len(self._agents))

    async def shutdown(self) -> None:
        """Shutdown the configuration service."""
        self.unsubscribe_all()
        self._initialized = False
        logger.info("config_service_shutdown")

    def _load_all_agents(self) -> None:
        """Load all agents by scanning ~/.tlon/*/config/agent.yaml."""
        self._agents = _yaml_load_all(self._tlon_home)

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
        filepath = paths.agent_config_file(agent_id)
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
            logger.error("agent_reload_failed", agent_id=agent_id, error=str(e), exc_info=True)

        return False

    def save_agent(self, agent: AgentConfig, create_data_dir: bool = True) -> None:
        """Save an agent to YAML file.

        Writes to ~/.tlon/{agent.id}/config/agent.yaml, then updates memory cache.
        """
        _yaml_save(agent, self._tlon_home)
        self._agents[agent.id] = agent
        logger.info("agent_saved", agent_id=agent.id)

    def delete_agent(self, agent_id: str, delete_data: bool = True) -> None:
        """Delete an agent.

        If delete_data is True, removes the entire ~/.tlon/{agent_id}/ directory.
        If False, only removes the config YAML file.
        """
        if delete_data:
            _yaml_delete(agent_id, self._tlon_home)
        else:
            yaml_file = paths.agent_config_file(agent_id)
            if yaml_file.exists():
                yaml_file.unlink()
        self._agents.pop(agent_id, None)
        logger.info("agent_deleted", agent_id=agent_id)


__all__ = [
    "AgentInfoProvider",
    "LLMConfig",
    "DockerRuntimeConfig",
    "MemoryConfig",
    "ToolConfig",
    "AgentConfig",
    "ConfigService",
]
