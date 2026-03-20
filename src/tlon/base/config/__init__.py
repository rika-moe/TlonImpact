"""Configuration module for Tlon.

Provides unified configuration management for agents and positions.

This module consolidates configuration-related functionality that was previously
scattered across multiple modules:
- tlon.positions.models (Position dataclass)
- tlon.agents.config (AgentConfig, LLMConfig, DockerRuntimeConfig)
- tlon.wizard.position_store (PositionStore)
- tlon.wizard.agent_store (AgentStore)

Usage:
    # Load configurations
    from tlon.config import ConfigLoader

    loader = ConfigLoader()
    positions = loader.load_all_positions()
    agents = loader.load_all_agents()
    templates = loader.load_prompt_templates()

    # Save configurations
    from tlon.config import ConfigSaver

    saver = ConfigSaver()
    saver.save_position(position)
    saver.save_agent(agent)

    # Data models
    from tlon.config import (
        PositionConfig,
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
    PositionConfig,
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
    "PositionConfig",
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
