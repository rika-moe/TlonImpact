"""Tests for config models."""

from tlon.base.config import (
    AgentConfig,
    DockerRuntimeConfig,
    LLMConfig,
    PromptOverrides,
    PromptTemplates,
)


class TestPromptOverrides:
    """Test PromptOverrides model."""

    def test_creation_with_all_fields(self):
        """Test creating PromptOverrides with all fields."""
        overrides = PromptOverrides(
            workflow="custom workflow",
            important_notes="custom notes",
            discipline_rules="custom rules",
        )
        assert overrides.workflow == "custom workflow"
        assert overrides.important_notes == "custom notes"
        assert overrides.discipline_rules == "custom rules"

    def test_creation_with_partial_fields(self):
        """Test creating PromptOverrides with only some fields."""
        overrides = PromptOverrides(workflow="only workflow")
        assert overrides.workflow == "only workflow"
        assert overrides.important_notes is None
        assert overrides.discipline_rules is None

    def test_to_dict_excludes_none(self):
        """Test to_dict excludes None values."""
        overrides = PromptOverrides(workflow="test")
        result = overrides.to_dict()
        assert result == {"workflow": "test"}
        assert "important_notes" not in result

    def test_from_dict(self):
        """Test creating from dictionary."""
        data = {"workflow": "wf", "discipline_rules": "dr"}
        overrides = PromptOverrides.from_dict(data)
        assert overrides.workflow == "wf"
        assert overrides.important_notes is None
        assert overrides.discipline_rules == "dr"

    def test_from_dict_empty(self):
        """Test creating from empty/None dictionary."""
        assert PromptOverrides.from_dict(None) is None
        assert PromptOverrides.from_dict({}) is None


class TestPromptTemplates:
    """Test PromptTemplates model."""

    def test_default_values(self):
        """Test default template values."""
        templates = PromptTemplates()
        assert "工作流程" in templates.workflow or "workflow" in templates.workflow.lower()
        assert (
            "重要提示" in templates.important_notes or "notes" in templates.important_notes.lower()
        )
        assert "规则" in templates.discipline_rules or "rules" in templates.discipline_rules.lower()

    def test_merge_with_no_overrides(self):
        """Test merge with no overrides returns same values."""
        templates = PromptTemplates()
        merged = templates.merge_with_overrides(None)
        assert merged.workflow == templates.workflow
        assert merged.important_notes == templates.important_notes
        assert merged.discipline_rules == templates.discipline_rules

    def test_merge_with_partial_overrides(self):
        """Test merge with partial overrides."""
        templates = PromptTemplates()
        overrides = PromptOverrides(workflow="custom workflow")
        merged = templates.merge_with_overrides(overrides)

        assert merged.workflow == "custom workflow"
        assert merged.important_notes == templates.important_notes  # unchanged
        assert merged.discipline_rules == templates.discipline_rules  # unchanged

    def test_merge_with_full_overrides(self):
        """Test merge with full overrides."""
        templates = PromptTemplates()
        overrides = PromptOverrides(
            workflow="wf",
            important_notes="in",
            discipline_rules="dr",
        )
        merged = templates.merge_with_overrides(overrides)

        assert merged.workflow == "wf"
        assert merged.important_notes == "in"
        assert merged.discipline_rules == "dr"

    def test_to_dict(self):
        """Test to_dict includes all fields."""
        templates = PromptTemplates(workflow="wf", important_notes="in", discipline_rules="dr")
        result = templates.to_dict()
        assert result["workflow"] == "wf"
        assert result["important_notes"] == "in"
        assert result["discipline_rules"] == "dr"

    def test_from_dict(self):
        """Test from_dict creates correct object."""
        data = {
            "workflow": "wf",
            "important_notes": "in",
            "discipline_rules": "dr",
        }
        templates = PromptTemplates.from_dict(data)
        assert templates.workflow == "wf"
        assert templates.important_notes == "in"
        assert templates.discipline_rules == "dr"

    def test_from_dict_uses_defaults_for_missing(self):
        """Test from_dict uses defaults for missing fields."""
        data = {"workflow": "wf"}
        templates = PromptTemplates.from_dict(data)
        assert templates.workflow == "wf"
        # Other fields should have default values
        assert templates.important_notes == PromptTemplates.important_notes


class TestLLMConfig:
    """Test LLMConfig model."""

    def test_default_values(self):
        """Test default LLM config values."""
        llm = LLMConfig()
        assert llm.model_name == "kimi-k2.5"
        assert llm.temperature == 0.7
        assert llm.max_tokens == 4096
        assert llm.top_p == 0.9

    def test_to_dict(self):
        """Test to_dict."""
        llm = LLMConfig(model_name="gpt-4", temperature=0.5)
        result = llm.to_dict()
        assert result["model_name"] == "gpt-4"
        assert result["temperature"] == 0.5

    def test_from_dict(self):
        """Test from_dict."""
        data = {"model_name": "gpt-4", "temperature": 0.5}
        llm = LLMConfig.from_dict(data)
        assert llm.model_name == "gpt-4"
        assert llm.temperature == 0.5


class TestDockerRuntimeConfig:
    """Test DockerRuntimeConfig model."""

    def test_default_values(self):
        """Test default Docker config values."""
        docker = DockerRuntimeConfig()
        assert docker.image == "ubuntu:22.04"
        assert docker.working_dir == "/workspace"
        assert docker.memory_limit == "512m"
        assert docker.cpu_limit == 1.0

    def test_to_dict(self):
        """Test to_dict."""
        docker = DockerRuntimeConfig(image="python:3.11")
        result = docker.to_dict()
        assert result["image"] == "python:3.11"
        assert "skills_dir" not in result  # None values excluded

    def test_to_dict_includes_skills_dir(self):
        """Test to_dict includes skills_dir when set."""
        docker = DockerRuntimeConfig(skills_dir="/skills")
        result = docker.to_dict()
        assert result["skills_dir"] == "/skills"

    def test_from_dict(self):
        """Test from_dict."""
        data = {"image": "python:3.11", "memory_limit": "1g"}
        docker = DockerRuntimeConfig.from_dict(data)
        assert docker.image == "python:3.11"
        assert docker.memory_limit == "1g"


class TestAgentConfig:
    """Test AgentConfig model."""

    def test_creation(self):
        """Test creating AgentConfig."""
        agent = AgentConfig(
            id="dev-001",
            name="林知遥",
            personality="细心",
        )
        assert agent.id == "dev-001"
        assert agent.name == "林知遥"
        assert agent.personality == "细心"

    def test_post_init_defaults(self):
        """Test __post_init__ sets default LLM and Docker configs."""
        agent = AgentConfig(id="dev-001", name="Test")
        assert agent.llm is not None
        assert agent.docker is not None
        assert isinstance(agent.llm, LLMConfig)
        assert isinstance(agent.docker, DockerRuntimeConfig)

    def test_to_dict(self):
        """Test to_dict."""
        agent = AgentConfig(
            id="dev-001",
            name="Test",
            skills=["bash"],
        )
        result = agent.to_dict()
        assert result["id"] == "dev-001"
        assert result["skills"] == ["bash"]
        assert "llm" in result
        assert "docker" in result

    def test_from_dict(self):
        """Test from_dict."""
        data = {
            "id": "dev-001",
            "name": "Test",
            "llm": {"model_name": "gpt-4"},
            "docker": {"image": "python:3.11"},
        }
        agent = AgentConfig.from_dict(data)
        assert agent.id == "dev-001"
        assert agent.llm.model_name == "gpt-4"
        assert agent.docker.image == "python:3.11"

    def test_from_dict_no_llm_docker(self):
        """Test from_dict without LLM and Docker configs - gets defaults via post_init."""
        data = {"id": "dev-001", "name": "Test"}
        agent = AgentConfig.from_dict(data)
        # from_dict returns None for missing llm/docker, but post_init sets defaults
        assert agent.llm is not None  # post_init sets default
        assert agent.docker is not None  # post_init sets default
