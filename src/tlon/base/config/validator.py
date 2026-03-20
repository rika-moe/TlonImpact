"""Configuration validation for Tlon.

Provides validation functions for Position, Agent, and Prompt configurations.
"""

from .models import AgentConfig, PositionConfig, PromptTemplates, RoleConfig


class ConfigValidator:
    """Validator for Tlon configuration data.

    Validates PositionConfig, AgentConfig, and PromptTemplates to ensure
    they meet the required format and constraints.
    """

    @staticmethod
    def validate_position_data(data: dict) -> tuple[bool, list[str]]:
        """Validate position data dictionary.

        Args:
            data: Position data dictionary

        Returns:
            Tuple of (is_valid, list_of_errors)
        """
        errors = []

        # Check required fields
        required_fields = ["type", "title", "jd"]
        for field in required_fields:
            if field not in data or not data[field]:
                errors.append(f"Missing required field: {field}")

        # Validate type format (alphanumeric and underscore)
        if "type" in data and data["type"]:
            type_val = data["type"]
            if not type_val.replace("_", "").replace("-", "").isalnum():
                errors.append("Position type must be alphanumeric with underscores/hyphens only")

        # Validate prompt_overrides if present
        if "prompt_overrides" in data and data["prompt_overrides"]:
            overrides = data["prompt_overrides"]
            if not isinstance(overrides, dict):
                errors.append("prompt_overrides must be a dictionary")
            else:
                valid_keys = {"workflow", "important_notes", "discipline_rules"}
                for key in overrides:
                    if key not in valid_keys:
                        errors.append(f"Invalid prompt_overrides key: {key}")
                    elif overrides[key] is not None and not isinstance(overrides[key], str):
                        errors.append(f"prompt_overrides.{key} must be a string or null")

        return len(errors) == 0, errors

    @staticmethod
    def validate_position_config(position: PositionConfig) -> tuple[bool, list[str]]:
        """Validate a PositionConfig object.

        Args:
            position: PositionConfig instance to validate

        Returns:
            Tuple of (is_valid, list_of_errors)
        """
        errors = []

        # Check required fields
        if not position.id:
            errors.append("Position id is required")
        if not position.type:
            errors.append("Position type is required")
        if not position.title:
            errors.append("Position title is required")
        if not position.jd:
            errors.append("Position jd (job description) is required")

        # Validate type format
        if position.type and not position.type.replace("_", "").replace("-", "").isalnum():
            errors.append("Position type must be alphanumeric with underscores/hyphens only")

        return len(errors) == 0, errors

    @staticmethod
    def validate_role_data(data: dict) -> tuple[bool, list[str]]:
        """Validate role data dictionary.

        Args:
            data: Role data dictionary

        Returns:
            Tuple of (is_valid, list_of_errors)
        """
        errors = []

        # Check required fields
        required_fields = ["title", "jd"]
        for field in required_fields:
            if field not in data or not data[field]:
                errors.append(f"Missing required role field: {field}")

        # Validate prompt_overrides if present
        if "prompt_overrides" in data and data["prompt_overrides"]:
            overrides = data["prompt_overrides"]
            if not isinstance(overrides, dict):
                errors.append("role.prompt_overrides must be a dictionary")
            else:
                valid_keys = {"workflow", "important_notes", "discipline_rules"}
                for key in overrides:
                    if key not in valid_keys:
                        errors.append(f"Invalid role.prompt_overrides key: {key}")
                    elif overrides[key] is not None and not isinstance(overrides[key], str):
                        errors.append(f"role.prompt_overrides.{key} must be a string or null")

        return len(errors) == 0, errors

    @staticmethod
    def validate_role_config(role: RoleConfig) -> tuple[bool, list[str]]:
        """Validate a RoleConfig object.

        Args:
            role: RoleConfig instance to validate

        Returns:
            Tuple of (is_valid, list_of_errors)
        """
        errors = []

        if not role.title:
            errors.append("Role title is required")
        if not role.jd:
            errors.append("Role jd (job description) is required")

        return len(errors) == 0, errors

    @staticmethod
    def validate_agent_data(data: dict) -> tuple[bool, list[str]]:
        """Validate agent data dictionary.

        Args:
            data: Agent data dictionary

        Returns:
            Tuple of (is_valid, list_of_errors)
        """
        errors = []

        # Check required fields (position_id is optional for backward compat)
        required_fields = ["id", "name"]
        for field in required_fields:
            if field not in data or not data[field]:
                errors.append(f"Missing required field: {field}")

        # Validate role sub-structure if present
        if "role" in data and data["role"]:
            if not isinstance(data["role"], dict):
                errors.append("role must be a dictionary")
            else:
                _, role_errors = ConfigValidator.validate_role_data(data["role"])
                errors.extend(role_errors)

        # Validate ID format
        if "id" in data and data["id"]:
            agent_id = data["id"]
            if not agent_id.replace("-", "").replace("_", "").isalnum():
                errors.append("Agent ID must be alphanumeric with hyphens/underscores only")

        # Validate LLM config if present
        if "llm" in data and data["llm"]:
            llm = data["llm"]
            if not isinstance(llm, dict):
                errors.append("LLM config must be a dictionary")
            elif "model_name" not in llm or not llm["model_name"]:
                errors.append("LLM config missing model_name")
            else:
                # Validate temperature range
                if "temperature" in llm:
                    temp = llm["temperature"]
                    if not isinstance(temp, (int, float)) or temp < 0 or temp > 2:
                        errors.append("LLM temperature must be between 0 and 2")

        # Validate Docker config if present
        if "docker" in data and data["docker"]:
            docker = data["docker"]
            if not isinstance(docker, dict):
                errors.append("Docker config must be a dictionary")
            elif "image" not in docker or not docker["image"]:
                errors.append("Docker config missing image")

        # Validate skills if present
        if "skills" in data and data["skills"]:
            skills = data["skills"]
            if not isinstance(skills, list):
                errors.append("Skills must be a list")
            else:
                for i, skill in enumerate(skills):
                    if not isinstance(skill, str):
                        errors.append(f"Skill at index {i} must be a string")

        return len(errors) == 0, errors

    @staticmethod
    def validate_agent_config(agent: AgentConfig) -> tuple[bool, list[str]]:
        """Validate an AgentConfig object.

        Args:
            agent: AgentConfig instance to validate

        Returns:
            Tuple of (is_valid, list_of_errors)
        """
        errors = []

        # Check required fields
        if not agent.id:
            errors.append("Agent id is required")
        if not agent.name:
            errors.append("Agent name is required")

        # Validate ID format
        if agent.id and not agent.id.replace("-", "").replace("_", "").isalnum():
            errors.append("Agent ID must be alphanumeric with hyphens/underscores only")

        # Validate role if present
        if agent.role is not None:
            _, role_errors = ConfigValidator.validate_role_config(agent.role)
            errors.extend(role_errors)

        return len(errors) == 0, errors

    @staticmethod
    def validate_prompt_templates(data: dict) -> tuple[bool, list[str]]:
        """Validate prompt templates data.

        Args:
            data: Prompt templates dictionary

        Returns:
            Tuple of (is_valid, list_of_errors)
        """
        errors = []

        if not isinstance(data, dict):
            errors.append("Prompt templates must be a dictionary")
            return False, errors

        # All fields are optional, but if present must be strings
        valid_keys = {"workflow", "important_notes", "discipline_rules"}

        for key, value in data.items():
            if key not in valid_keys:
                errors.append(f"Invalid template key: {key}")
            elif value is not None and not isinstance(value, str):
                errors.append(f"Template {key} must be a string or null")

        return len(errors) == 0, errors

    @staticmethod
    def validate_prompt_templates_config(templates: PromptTemplates) -> tuple[bool, list[str]]:
        """Validate a PromptTemplates object.

        Args:
            templates: PromptTemplates instance to validate

        Returns:
            Tuple of (is_valid, list_of_errors)
        """
        errors = []

        # All fields should be strings
        if not isinstance(templates.workflow, str):
            errors.append("workflow must be a string")
        if not isinstance(templates.important_notes, str):
            errors.append("important_notes must be a string")
        if not isinstance(templates.discipline_rules, str):
            errors.append("discipline_rules must be a string")

        return len(errors) == 0, errors


def validate_yaml_structure(data: dict, expected_key: str) -> tuple[bool, list[str]]:
    """Validate YAML file has expected structure.

    Args:
        data: Loaded YAML data
        expected_key: Expected top-level key (e.g., "position", "agent")

    Returns:
        Tuple of (is_valid, list_of_errors)
    """
    errors = []

    if not isinstance(data, dict):
        errors.append("YAML file must contain a dictionary")
        return False, errors

    if expected_key not in data:
        errors.append(f"Missing required key: '{expected_key}'")
    elif not isinstance(data[expected_key], dict):
        errors.append(f"'{expected_key}' must be a dictionary")

    return len(errors) == 0, errors


__all__ = [
    "ConfigValidator",
    "validate_yaml_structure",
]
