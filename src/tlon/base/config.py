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


class PromptOverrides(BaseModel):
    """Role-level prompt overrides for complete replacement of global templates."""

    workflow: str | None = None
    important_notes: str | None = None
    discipline_rules: str | None = None


class PromptTemplates(BaseModel):
    """Global default prompt templates."""

    workflow: str = """对于普通用户的消息，自由响应。
对于同事：
- 收到直接@时，必须响应
- 未被@时，观察对话决定是否参与
- 讨论偏离主题时，可以主动介入

被@时的工作流程：
1. 分析问题：理解问题背景和需求
2. 规划方案：制定解决思路
3. 如需工具：调用 bash/python 工具辅助分析
4. 给出答案：提供完整、准确、有帮助的回复
"""

    important_notes: str = """消息格式说明：
- 来自用户的普通消息：user: 消息内容
- 来自同事的广播消息：agent_name: 消息内容
- 被@的消息格式：agent_name @你: 消息内容

重要提示：
- 正常对话时，使用自然语言回复即可
- 必须使用 completeDialog 工具结束对话，即使没有工具调用
"""

    discipline_rules: str = """严格遵守以下规则：
1. 禁止捏造信息或假装执行操作
2. 不确定时主动询问或查资料
3. 代码和配置必须验证后再提交
4. 操作生产环境前必须确认
"""

    def merge_with_overrides(self, overrides: PromptOverrides | None) -> "PromptTemplates":
        """Create new templates with overrides applied (complete replacement)."""
        if not overrides:
            return self
        return PromptTemplates(
            workflow=overrides.workflow if overrides.workflow is not None else self.workflow,
            important_notes=overrides.important_notes
            if overrides.important_notes is not None
            else self.important_notes,
            discipline_rules=overrides.discipline_rules
            if overrides.discipline_rules is not None
            else self.discipline_rules,
        )


# =============================================================================
# Role Configuration
# =============================================================================


class RoleConfig(BaseModel):
    """Role configuration inlined directly into AgentConfig."""

    title: str = ""
    jd: str = Field(default="", description="Job Description")
    department: str = ""
    rules: list[str] = Field(default_factory=list)
    contact_when: str = ""
    prompt_overrides: PromptOverrides | None = None


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
    skills_dir: str | None = None


class AgentConfig(BaseModel):
    """Complete agent configuration."""

    id: str
    name: str
    personality: str = ""
    role: RoleConfig | None = None
    llm: LLMConfig = Field(default_factory=LLMConfig)
    docker: DockerRuntimeConfig = Field(default_factory=DockerRuntimeConfig)
    memory: Any = None
    skills: list[str] = Field(default_factory=list)
    respond_to_all: bool = False
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
        self._templates: PromptTemplates | None = None
        logger.info("ConfigService constructed")

    async def initialize(self) -> None:
        """Initialize by loading all configurations."""
        logger.info("Loading initial configuration...")
        self._load_all_agents()
        self._load_prompt_templates()
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
            return config.proactive or config.respond_to_all
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

    def _load_prompt_templates(self) -> None:
        """Load global prompt templates from YAML file."""
        filepath = self._config_dir / "prompt_templates.yaml"

        if not filepath.exists():
            self._templates = PromptTemplates()
            return

        try:
            data = yaml.safe_load(filepath.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "templates" in data:
                self._templates = PromptTemplates.model_validate(data["templates"])
            else:
                self._templates = PromptTemplates()
        except Exception as e:
            logger.warning(f"Failed to load prompt templates: {e}. Using defaults.")
            self._templates = PromptTemplates()

    def get_prompt_templates(self) -> PromptTemplates:
        """Get global prompt templates."""
        if self._templates is None:
            self._load_prompt_templates()
        return self._templates or PromptTemplates()

    def get_templates_for_role(self, role: RoleConfig | None = None) -> PromptTemplates:
        """Get merged templates for a role (with overrides applied)."""
        templates = self.get_prompt_templates()
        if role and role.prompt_overrides:
            return templates.merge_with_overrides(role.prompt_overrides)
        return templates

    def get_templates_for_agent(self, agent: AgentConfig) -> PromptTemplates:
        """Get merged templates for an agent's role."""
        return self.get_templates_for_role(agent.role)

    def save_prompt_templates(self, templates: PromptTemplates) -> None:
        """Save global prompt templates to YAML file."""
        filepath = self._config_dir / "prompt_templates.yaml"
        self._config_dir.mkdir(parents=True, exist_ok=True)
        data = {"templates": templates.model_dump()}
        filepath.write_text(
            yaml.dump(
                data, allow_unicode=True, sort_keys=False, default_flow_style=False, indent=2
            ),
            encoding="utf-8",
        )
        self._templates = templates
        logger.info(f"Prompt templates saved to {filepath}")


__all__ = [
    "AgentInfoProvider",
    "PromptOverrides",
    "PromptTemplates",
    "RoleConfig",
    "LLMConfig",
    "DockerRuntimeConfig",
    "AgentConfig",
    "ConfigService",
]
