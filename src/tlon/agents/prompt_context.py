"""Prompt context data object for SystemPromptBuilder.

Decouples SystemPromptBuilder from Agent by providing a plain data
container with everything needed to construct a system prompt.
"""

from dataclasses import dataclass, field
from typing import Any

from tlon.base.config.models import AgentConfig
from tlon.base.config.prompts import PromptTemplateManager


@dataclass
class AgentPromptContext:
    """Pure data object carrying all information needed to build a system prompt.

    No references to Agent or any infrastructure components.
    All async data (memory blocks) must be pre-fetched before constructing this.

    Attributes:
        config: The agent's own configuration (identity, role, personality).
        team_members: Configs of all other agents in the team.
        tool_definitions: OpenAI-format tool definitions from ToolExecutor.
        memory_blocks: Pre-fetched memory context blocks.
        prompt_manager: Template manager for workflow and important_notes sections.
    """

    config: AgentConfig
    team_members: list[AgentConfig]
    tool_definitions: list[dict[str, Any]]
    memory_blocks: list
    prompt_manager: PromptTemplateManager
    extra: dict[str, Any] = field(default_factory=dict)
