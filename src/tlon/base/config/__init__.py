"""Configuration module for Tlon.

Provides unified configuration management for agents.

Usage:
    # Load configurations
    from tlon.config import ConfigLoader

    loader = ConfigLoader()
    agents = loader.load_all_agents()
    templates = loader.load_prompt_templates()

    # Save configurations
    from tlon.config import ConfigSaver

    saver = ConfigSaver()
    saver.save_agent(agent)

    # Data models
    from tlon.config import (
        RoleConfig,
        AgentConfig,
        PromptTemplates,
        PromptOverrides,
        LLMConfig,
        DockerRuntimeConfig,
    )
"""

from .loader import ConfigLoader, ConfigLoadError
from .models import (
    AgentConfig,
    DockerRuntimeConfig,
    LLMConfig,
    PromptOverrides,
    PromptTemplates,
    RoleConfig,
)
from .prompts import PromptTemplateManager
from .saver import ConfigSaveError, ConfigSaver
from .validator import ConfigValidator

__all__ = [
    # Data models
    "RoleConfig",
    "AgentConfig",
    "PromptTemplates",
    "PromptOverrides",
    "LLMConfig",
    "DockerRuntimeConfig",
    # Loading and saving
    "ConfigLoader",
    "ConfigSaver",
    "ConfigLoadError",
    "ConfigSaveError",
    # Validation
    "ConfigValidator",
    # Prompt management
    "PromptTemplateManager",
]
