"""Tests for config validator."""

from tlon.base.config import ConfigValidator
from tlon.base.config.models import AgentConfig, PositionConfig
from tlon.base.config.validator import validate_yaml_structure


class TestValidatePositionData:
    """Test validate_position_data function."""

    def test_valid_position_data(self):
        """Test valid position data passes validation."""
        data = {
            "type": "developer",
            "title": "开发工程师",
            "jd": "负责开发",
        }
        is_valid, errors = ConfigValidator.validate_position_data(data)
        assert is_valid is True
        assert len(errors) == 0

    def test_missing_required_fields(self):
        """Test validation catches missing required fields."""
        data = {"type": "developer"}  # missing title and jd
        is_valid, errors = ConfigValidator.validate_position_data(data)
        assert is_valid is False
        assert any("title" in e for e in errors)
        assert any("jd" in e for e in errors)

    def test_invalid_type_format(self):
        """Test validation catches invalid type format."""
        data = {
            "type": "dev@123",  # invalid character
            "title": "开发",
            "jd": "负责开发",
        }
        is_valid, errors = ConfigValidator.validate_position_data(data)
        assert is_valid is False
        assert any("alphanumeric" in e for e in errors)

    def test_valid_type_with_underscore(self):
        """Test type with underscore is valid."""
        data = {
            "type": "project_manager",
            "title": "项目经理",
            "jd": "负责管理",
        }
        is_valid, errors = ConfigValidator.validate_position_data(data)
        assert is_valid is True

    def test_valid_type_with_hyphen(self):
        """Test type with hyphen is valid."""
        data = {
            "type": "project-manager",
            "title": "项目经理",
            "jd": "负责管理",
        }
        is_valid, errors = ConfigValidator.validate_position_data(data)
        assert is_valid is True

    def test_invalid_prompt_overrides_type(self):
        """Test validation catches non-dict prompt_overrides."""
        data = {
            "type": "developer",
            "title": "开发",
            "jd": "负责开发",
            "prompt_overrides": "not a dict",
        }
        is_valid, errors = ConfigValidator.validate_position_data(data)
        assert is_valid is False
        assert any("must be a dictionary" in e for e in errors)

    def test_invalid_prompt_overrides_key(self):
        """Test validation catches invalid prompt_overrides keys."""
        data = {
            "type": "developer",
            "title": "开发",
            "jd": "负责开发",
            "prompt_overrides": {"invalid_key": "value"},
        }
        is_valid, errors = ConfigValidator.validate_position_data(data)
        assert is_valid is False
        assert any("Invalid prompt_overrides key" in e for e in errors)

    def test_invalid_prompt_overrides_value_type(self):
        """Test validation catches non-string prompt_overrides values."""
        data = {
            "type": "developer",
            "title": "开发",
            "jd": "负责开发",
            "prompt_overrides": {"workflow": 123},
        }
        is_valid, errors = ConfigValidator.validate_position_data(data)
        assert is_valid is False
        assert any("must be a string" in e for e in errors)


class TestValidatePositionConfig:
    """Test validate_position_config function."""

    def test_valid_position(self):
        """Test valid PositionConfig passes validation."""
        position = PositionConfig(
            id="dev",
            type="developer",
            title="开发",
            jd="负责开发",
        )
        is_valid, errors = ConfigValidator.validate_position_config(position)
        assert is_valid is True
        assert len(errors) == 0

    def test_missing_id(self):
        """Test validation catches missing id."""
        position = PositionConfig(
            id="",
            type="developer",
            title="开发",
            jd="负责开发",
        )
        is_valid, errors = ConfigValidator.validate_position_config(position)
        assert is_valid is False
        assert any("id is required" in e for e in errors)

    def test_missing_title(self):
        """Test validation catches missing title."""
        position = PositionConfig(
            id="dev",
            type="developer",
            title="",
            jd="负责开发",
        )
        is_valid, errors = ConfigValidator.validate_position_config(position)
        assert is_valid is False
        assert any("title is required" in e for e in errors)


class TestValidateAgentData:
    """Test validate_agent_data function."""

    def test_valid_agent_data(self):
        """Test valid agent data passes validation."""
        data = {
            "id": "dev-001",
            "name": "林知遥",
            "position_id": "developer",
        }
        is_valid, errors = ConfigValidator.validate_agent_data(data)
        assert is_valid is True
        assert len(errors) == 0

    def test_missing_required_fields(self):
        """Test validation catches missing required fields."""
        data = {"id": "dev-001"}  # missing name (position_id is now optional)
        is_valid, errors = ConfigValidator.validate_agent_data(data)
        assert is_valid is False
        assert any("name" in e for e in errors)

    def test_invalid_id_format(self):
        """Test validation catches invalid ID format."""
        data = {
            "id": "dev@001",  # invalid character
            "name": "Test",
            "position_id": "developer",
        }
        is_valid, errors = ConfigValidator.validate_agent_data(data)
        assert is_valid is False
        assert any("alphanumeric" in e for e in errors)

    def test_valid_llm_config(self):
        """Test valid LLM config passes validation."""
        data = {
            "id": "dev-001",
            "name": "Test",
            "position_id": "developer",
            "llm": {"model_name": "gpt-4", "temperature": 0.7},
        }
        is_valid, errors = ConfigValidator.validate_agent_data(data)
        assert is_valid is True

    def test_invalid_llm_config_type(self):
        """Test validation catches non-dict LLM config."""
        data = {
            "id": "dev-001",
            "name": "Test",
            "position_id": "developer",
            "llm": "not a dict",
        }
        is_valid, errors = ConfigValidator.validate_agent_data(data)
        assert is_valid is False
        assert any("must be a dictionary" in e for e in errors)

    def test_missing_llm_model_name(self):
        """Test validation catches missing LLM model_name."""
        data = {
            "id": "dev-001",
            "name": "Test",
            "position_id": "developer",
            "llm": {"temperature": 0.7},
        }
        is_valid, errors = ConfigValidator.validate_agent_data(data)
        assert is_valid is False
        assert any("model_name" in e for e in errors)

    def test_invalid_temperature_range(self):
        """Test validation catches invalid temperature."""
        data = {
            "id": "dev-001",
            "name": "Test",
            "position_id": "developer",
            "llm": {"model_name": "gpt-4", "temperature": 3.0},
        }
        is_valid, errors = ConfigValidator.validate_agent_data(data)
        assert is_valid is False
        assert any("temperature" in e for e in errors)

    def test_invalid_skills_type(self):
        """Test validation catches non-list skills."""
        data = {
            "id": "dev-001",
            "name": "Test",
            "position_id": "developer",
            "skills": "not a list",
        }
        is_valid, errors = ConfigValidator.validate_agent_data(data)
        assert is_valid is False
        assert any("must be a list" in e for e in errors)

    def test_invalid_skill_element_type(self):
        """Test validation catches non-string skill elements."""
        data = {
            "id": "dev-001",
            "name": "Test",
            "position_id": "developer",
            "skills": ["bash", 123],
        }
        is_valid, errors = ConfigValidator.validate_agent_data(data)
        assert is_valid is False
        assert any("must be a string" in e for e in errors)


class TestValidateAgentConfig:
    """Test validate_agent_config function."""

    def test_valid_agent(self):
        """Test valid AgentConfig passes validation."""
        agent = AgentConfig(
            id="dev-001",
            name="林知遥",
            position_id="developer",
        )
        is_valid, errors = ConfigValidator.validate_agent_config(agent)
        assert is_valid is True

    def test_missing_id(self):
        """Test validation catches missing id."""
        agent = AgentConfig(
            id="",
            name="林知遥",
            position_id="developer",
        )
        is_valid, errors = ConfigValidator.validate_agent_config(agent)
        assert is_valid is False
        assert any("id is required" in e for e in errors)


class TestValidatePromptTemplates:
    """Test validate_prompt_templates function."""

    def test_valid_templates(self):
        """Test valid templates data passes validation."""
        data = {
            "workflow": "custom workflow",
            "important_notes": "custom notes",
            "discipline_rules": "custom rules",
        }
        is_valid, errors = ConfigValidator.validate_prompt_templates(data)
        assert is_valid is True

    def test_partial_templates(self):
        """Test partial templates data passes validation."""
        data = {"workflow": "only workflow"}
        is_valid, errors = ConfigValidator.validate_prompt_templates(data)
        assert is_valid is True

    def test_empty_templates(self):
        """Test empty templates data passes validation."""
        data = {}
        is_valid, errors = ConfigValidator.validate_prompt_templates(data)
        assert is_valid is True

    def test_invalid_key(self):
        """Test validation catches invalid keys."""
        data = {"invalid_key": "value"}
        is_valid, errors = ConfigValidator.validate_prompt_templates(data)
        assert is_valid is False
        assert any("Invalid template key" in e for e in errors)

    def test_invalid_value_type(self):
        """Test validation catches non-string values."""
        data = {"workflow": 123}
        is_valid, errors = ConfigValidator.validate_prompt_templates(data)
        assert is_valid is False


class TestValidateYamlStructure:
    """Test validate_yaml_structure function."""

    def test_valid_structure(self):
        """Test valid YAML structure passes validation."""
        data = {"position": {"type": "developer"}}
        is_valid, errors = validate_yaml_structure(data, "position")
        assert is_valid is True

    def test_missing_required_key(self):
        """Test validation catches missing required key."""
        data = {"other": {}}
        is_valid, errors = validate_yaml_structure(data, "position")
        assert is_valid is False
        assert any("position" in e for e in errors)

    def test_non_dict_value(self):
        """Test validation catches non-dict value."""
        data = {"position": "not a dict"}
        is_valid, errors = validate_yaml_structure(data, "position")
        assert is_valid is False

    def test_non_dict_data(self):
        """Test validation catches non-dict data."""
        data = "not a dict"
        is_valid, errors = validate_yaml_structure(data, "position")
        assert is_valid is False
