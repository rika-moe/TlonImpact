"""Prompt context data object for SystemPromptBuilder.

Decouples SystemPromptBuilder from Agent by providing a plain data
container with everything needed to construct a system prompt.
"""

from dataclasses import dataclass


@dataclass
class AgentPromptContext:
    """Pure data object carrying all information needed to build a system prompt.

    No references to Agent or any infrastructure components.

    Attributes:
        agent_name: The agent's display name.
        agent_home: Path to agent's ~/.tlon/{id}/ directory (for reading .md files).
    """

    agent_name: str
    agent_home: str
