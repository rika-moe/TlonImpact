"""Configuration data models for Tlon.

This module defines all configuration data models used across the system.
It provides a centralized location for configuration-related dataclasses.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


# =============================================================================
# Prompt Template Models
# =============================================================================

@dataclass
class PromptOverrides:
    """Position-level prompt overrides for complete replacement of global templates.
    
    When a Position provides prompt_overrides, it completely replaces the corresponding
    global template (not appends to it).
    
    Attributes:
        workflow: Override for workflow template
        important_notes: Override for important_notes template  
        discipline_rules: Override for discipline_rules template
    """
    workflow: Optional[str] = None
    important_notes: Optional[str] = None
    discipline_rules: Optional[str] = None
    
    def to_dict(self) -> dict:
        """Convert to dictionary, excluding None values."""
        result = {}
        if self.workflow is not None:
            result["workflow"] = self.workflow
        if self.important_notes is not None:
            result["important_notes"] = self.important_notes
        if self.discipline_rules is not None:
            result["discipline_rules"] = self.discipline_rules
        return result
    
    @classmethod
    def from_dict(cls, data: Optional[dict]) -> Optional["PromptOverrides"]:
        """Create from dictionary."""
        if not data:
            return None
        return cls(
            workflow=data.get("workflow"),
            important_notes=data.get("important_notes"),
            discipline_rules=data.get("discipline_rules"),
        )


@dataclass
class PromptTemplates:
    """Global default prompt templates.
    
    These templates serve as the base defaults that can be overridden by
    Position configurations via prompt_overrides.
    
    Attributes:
        workflow: Default workflow description template
        important_notes: Default important notes template
        discipline_rules: Default discipline rules template
    """
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
    
    def merge_with_overrides(self, overrides: Optional[PromptOverrides]) -> "PromptTemplates":
        """Create new templates with overrides applied.
        
        Overrides completely replace the corresponding template (not append).
        
        Args:
            overrides: Position-level overrides
            
        Returns:
            New PromptTemplates with overrides applied
        """
        if not overrides:
            return self
        
        return PromptTemplates(
            workflow=overrides.workflow if overrides.workflow is not None else self.workflow,
            important_notes=overrides.important_notes if overrides.important_notes is not None else self.important_notes,
            discipline_rules=overrides.discipline_rules if overrides.discipline_rules is not None else self.discipline_rules,
        )
    
    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "workflow": self.workflow,
            "important_notes": self.important_notes,
            "discipline_rules": self.discipline_rules,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "PromptTemplates":
        """Create from dictionary."""
        return cls(
            workflow=data.get("workflow", cls.workflow),
            important_notes=data.get("important_notes", cls.important_notes),
            discipline_rules=data.get("discipline_rules", cls.discipline_rules),
        )


# =============================================================================
# Position Configuration
# =============================================================================

@dataclass
class PositionConfig:
    """Position (Job Role) configuration.
    
    A Position defines the standardized job description, responsibilities,
    and behavior for a type of role. Multiple agents can share the same position.
    
    Attributes:
        id: Unique position identifier (e.g., "developer", "project_manager")
        type: Position type code (usually same as id)
        title: Human-readable position title (e.g., "开发工程师")
        jd: Job description and responsibilities (岗位职责)
        department: Optional department name
        proactive: Whether agents in this position actively participate
        rules: List of behavior rules for this position
        default_skills: Default skills for agents in this position
        contact_when: When to contact this role
        prompt_overrides: Optional overrides for global prompt templates
    """
    id: str
    type: str
    title: str = ""
    jd: str = ""  # Job Description
    department: str = ""
    proactive: bool = False
    rules: List[str] = field(default_factory=list)
    default_skills: List[str] = field(default_factory=list)
    contact_when: str = ""  # 何时应该联系该角色
    prompt_overrides: Optional[PromptOverrides] = None
    
    def to_dict(self) -> dict:
        """Convert position to dictionary."""
        result = {
            "id": self.id,
            "type": self.type,
            "title": self.title,
            "jd": self.jd,
            "department": self.department,
            "proactive": self.proactive,
            "rules": self.rules,
            "default_skills": self.default_skills,
            "contact_when": self.contact_when,
        }
        if self.prompt_overrides:
            result["prompt_overrides"] = self.prompt_overrides.to_dict()
        return result
    
    @classmethod
    def from_dict(cls, data: dict) -> "PositionConfig":
        """Create position from dictionary."""
        return cls(
            id=data["id"],
            type=data.get("type", data["id"]),
            title=data.get("title", ""),
            jd=data.get("jd", data.get("description", "")),
            department=data.get("department", ""),
            proactive=data.get("proactive", False),
            rules=data.get("rules", []),
            default_skills=data.get("default_skills", data.get("skills", [])),
            contact_when=data.get("contact_when", ""),
            prompt_overrides=PromptOverrides.from_dict(data.get("prompt_overrides")),
        )
    
    def get_display_name(self) -> str:
        """Get the display name for this position."""
        return self.title or self.id


# =============================================================================
# Agent Configuration
# =============================================================================

@dataclass
class LLMConfig:
    """LLM configuration for an agent."""
    
    model_name: str = "kimi-k2.5"
    temperature: float = 0.7
    max_tokens: int = 4096
    top_p: float = 0.9
    
    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "model_name": self.model_name,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "top_p": self.top_p,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "LLMConfig":
        """Create from dictionary."""
        return cls(
            model_name=data.get("model_name", "kimi-k2.5"),
            temperature=data.get("temperature", 0.7),
            max_tokens=data.get("max_tokens", 4096),
            top_p=data.get("top_p", 0.9),
        )


@dataclass
class DockerRuntimeConfig:
    """Docker runtime configuration."""
    
    image: str = "ubuntu:22.04"
    working_dir: str = "/workspace"
    memory_limit: str = "512m"
    cpu_limit: float = 1.0
    network_mode: str = "host"
    skills_dir: Optional[str] = None
    
    def to_dict(self) -> dict:
        """Convert to dictionary."""
        result = {
            "image": self.image,
            "working_dir": self.working_dir,
            "memory_limit": self.memory_limit,
            "cpu_limit": self.cpu_limit,
            "network_mode": self.network_mode,
        }
        if self.skills_dir:
            result["skills_dir"] = self.skills_dir
        return result
    
    @classmethod
    def from_dict(cls, data: dict) -> "DockerRuntimeConfig":
        """Create from dictionary."""
        return cls(
            image=data.get("image", "ubuntu:22.04"),
            working_dir=data.get("working_dir", "/workspace"),
            memory_limit=data.get("memory_limit", "512m"),
            cpu_limit=data.get("cpu_limit", 1.0),
            network_mode=data.get("network_mode", "host"),
            skills_dir=data.get("skills_dir"),
        )


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
        memory: Memory configuration (Any type for flexibility)
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
        if self.llm is None:
            self.llm = LLMConfig()
        if self.docker is None:
            self.docker = DockerRuntimeConfig()
    
    def to_dict(self) -> dict:
        """Convert to dictionary."""
        result = {
            "id": self.id,
            "name": self.name,
            "personality": self.personality,
            "position_id": self.position_id,
            "skills": self.skills,
            "respond_to_all": self.respond_to_all,
        }
        if self.llm:
            result["llm"] = self.llm.to_dict()
        if self.docker:
            result["docker"] = self.docker.to_dict()
        return result
    
    @classmethod
    def from_dict(cls, data: dict) -> "AgentConfig":
        """Create from dictionary."""
        # Parse LLM config
        llm = None
        if "llm" in data and data["llm"]:
            llm = LLMConfig.from_dict(data["llm"])
        
        # Parse Docker config
        docker = None
        if "docker" in data and data["docker"]:
            docker = DockerRuntimeConfig.from_dict(data["docker"])
        
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            personality=data.get("personality", ""),
            position_id=data.get("position_id", ""),
            llm=llm,
            docker=docker,
            memory=data.get("memory"),
            skills=data.get("skills", []),
            respond_to_all=data.get("respond_to_all", False),
        )
    
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
    "PromptOverrides",
    "PromptTemplates", 
    "PositionConfig",
    "LLMConfig",
    "DockerRuntimeConfig",
    "AgentConfig",
]
