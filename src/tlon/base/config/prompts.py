"""Prompt template management for Tlon.

Provides centralized prompt template management with support for:
- Global default templates
- Role-level overrides (complete replacement)
- Template composition for system prompts
"""

from typing import TYPE_CHECKING

from ..logger import get_logger
from .loader import ConfigLoader
from .models import PromptTemplates, RoleConfig

if TYPE_CHECKING:
    from .models import AgentConfig

logger = get_logger(__name__)


class PromptTemplateManager:
    """Manages prompt templates with two-layer architecture.

    Architecture:
        1. Global Templates (config/prompt_templates.yaml) - Default values
        2. Role Overrides (AgentConfig.role.prompt_overrides) - Complete replacement

    Override Strategy:
        - Role's `prompt_overrides` completely replaces global templates
        - Not append, but full replacement
        - Each of workflow, important_notes, discipline_rules can be overridden independently

    Attributes:
        loader: ConfigLoader instance for loading configurations
        _global_templates: Cached global templates
    """

    def __init__(self, loader: ConfigLoader | None = None):
        """Initialize the prompt template manager.

        Args:
            loader: ConfigLoader instance (creates default if None)
        """
        self.loader = loader or ConfigLoader()
        self._global_templates: PromptTemplates | None = None

    def load_global_templates(self, force_reload: bool = False) -> PromptTemplates:
        """Load global prompt templates.

        Args:
            force_reload: Force reload from disk

        Returns:
            Global PromptTemplates
        """
        if self._global_templates is None or force_reload:
            self._global_templates = self.loader.load_prompt_templates(force_reload=force_reload)
        return self._global_templates

    def get_templates_for_role(self, role: RoleConfig | None = None) -> PromptTemplates:
        """Get merged templates for a role.

        Applies role-level overrides to global templates.
        Overrides are complete replacements (not append).

        Args:
            role: RoleConfig with optional prompt_overrides

        Returns:
            Merged PromptTemplates
        """
        global_templates = self.load_global_templates()

        if role and role.prompt_overrides:
            return global_templates.merge_with_overrides(role.prompt_overrides)

        return global_templates

    def get_templates_for_agent(self, agent: "AgentConfig") -> PromptTemplates:
        """Get merged templates for an agent's role.

        Args:
            agent: AgentConfig instance

        Returns:
            Merged PromptTemplates
        """
        return self.get_templates_for_role(role=agent.role)

    def build_workflow_prompt(self, role: RoleConfig | None = None) -> str:
        """Build the workflow prompt section.

        Args:
            role: RoleConfig with optional prompt_overrides

        Returns:
            Workflow prompt string
        """
        templates = self.get_templates_for_role(role)
        return f"""## 工作流程

{templates.workflow}"""

    def build_important_notes_prompt(self, role: RoleConfig | None = None) -> str:
        """Build the important notes prompt section.

        Args:
            role: RoleConfig with optional prompt_overrides

        Returns:
            Important notes prompt string
        """
        templates = self.get_templates_for_role(role)
        return f"""## 重要提示

{templates.important_notes}"""

    def build_discipline_rules_prompt(
        self,
        role: RoleConfig | None = None,
        role_rules: list[str] | None = None,
    ) -> str:
        """Build the discipline rules prompt section.

        Combines global/role discipline rules with role-specific rules.

        Args:
            role: RoleConfig with optional prompt_overrides
            role_rules: Additional role-specific rules

        Returns:
            Discipline rules prompt string
        """
        templates = self.get_templates_for_role(role)

        rules_text = templates.discipline_rules

        if role_rules:
            additional_rules = "\n".join(f"{i + 5}. {rule}" for i, rule in enumerate(role_rules))
            rules_text = f"{rules_text}\n{additional_rules}"

        return f"""## 工作纪律

{rules_text}"""


__all__ = [
    "PromptTemplateManager",
]
