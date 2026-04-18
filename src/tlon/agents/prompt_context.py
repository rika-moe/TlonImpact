"""Prompt context data object for SystemPromptBuilder.

Decouples SystemPromptBuilder from Agent by providing a plain data
container with everything needed to construct a system prompt.
"""

from dataclasses import dataclass

from tlon.base.config import AgentConfig


@dataclass
class AgentPromptContext:
    """Pure data object carrying all information needed to build a system prompt.

    No references to Agent or any infrastructure components.

    Attributes:
        config: The agent's own configuration (identity, profile, behavior).
        team_profiles: List of (name, profile) tuples for all other agents.
    """

    config: AgentConfig
    team_profiles: list[tuple[str, str]]
