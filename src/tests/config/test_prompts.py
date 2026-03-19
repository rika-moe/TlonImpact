"""Tests for prompt template manager."""

import tempfile
from pathlib import Path

import yaml

from tlon.base.config import (
    ConfigLoader,
    PositionConfig,
    PromptOverrides,
    PromptTemplateManager,
)


class TestPromptTemplateManagerInit:
    """Test PromptTemplateManager initialization."""

    def test_default_init(self):
        """Test default initialization creates loader."""
        manager = PromptTemplateManager()
        assert manager.loader is not None
        assert isinstance(manager.loader, ConfigLoader)

    def test_custom_loader(self):
        """Test initialization with custom loader."""
        loader = ConfigLoader()
        manager = PromptTemplateManager(loader)
        assert manager.loader is loader


class TestLoadGlobalTemplates:
    """Test loading global templates."""

    def test_load_default_templates(self):
        """Test loading default templates."""
        manager = PromptTemplateManager()
        templates = manager.load_global_templates()

        assert templates is not None
        assert len(templates.workflow) > 0
        assert len(templates.important_notes) > 0
        assert len(templates.discipline_rules) > 0

    def test_caching(self):
        """Test templates are cached."""
        manager = PromptTemplateManager()

        templates1 = manager.load_global_templates()
        templates2 = manager.load_global_templates()

        assert templates1 is templates2

    def test_force_reload(self):
        """Test force reload bypasses cache."""
        manager = PromptTemplateManager()

        templates1 = manager.load_global_templates()
        templates2 = manager.load_global_templates(force_reload=True)

        # Should be equal but not the same object
        assert templates1.workflow == templates2.workflow


class TestGetTemplatesForPosition:
    """Test getting templates for position."""

    def test_get_without_overrides(self):
        """Test getting templates without position overrides."""
        manager = PromptTemplateManager()
        position = PositionConfig(
            id="dev",
            type="developer",
            title="开发",
            jd="负责开发",
        )

        templates = manager.get_templates_for_position(position)

        # Should return global defaults
        global_templates = manager.load_global_templates()
        assert templates.workflow == global_templates.workflow

    def test_get_with_overrides(self):
        """Test getting templates with position overrides."""
        manager = PromptTemplateManager()
        position = PositionConfig(
            id="dev",
            type="developer",
            title="开发",
            jd="负责开发",
            prompt_overrides=PromptOverrides(workflow="custom workflow"),
        )

        templates = manager.get_templates_for_position(position)

        assert templates.workflow == "custom workflow"

    def test_get_by_type(self):
        """Test getting templates by position type."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a position file with overrides
            position_data = {
                "position": {
                    "type": "developer",
                    "title": "开发",
                    "jd": "负责开发",
                    "prompt_overrides": {"workflow": "developer specific workflow"},
                }
            }
            with open(Path(tmpdir) / "developer.yaml", "w") as f:
                yaml.dump(position_data, f)

            loader = ConfigLoader(positions_dir=tmpdir)
            manager = PromptTemplateManager(loader)

            templates = manager.get_templates_for_position(position_type="developer")

            assert templates.workflow == "developer specific workflow"


class TestBuildWorkflowPrompt:
    """Test building workflow prompt section."""

    def test_build_workflow_prompt(self):
        """Test building workflow prompt."""
        manager = PromptTemplateManager()
        prompt = manager.build_workflow_prompt()

        assert "## 工作流程" in prompt
        assert len(prompt) > len("## 工作流程\n\n")


class TestBuildImportantNotesPrompt:
    """Test building important notes prompt section."""

    def test_build_important_notes(self):
        """Test building important notes prompt."""
        manager = PromptTemplateManager()
        prompt = manager.build_important_notes_prompt()

        assert "## 重要提示" in prompt
        assert len(prompt) > len("## 重要提示\n\n")


class TestBuildDisciplineRulesPrompt:
    """Test building discipline rules prompt section."""

    def test_build_discipline_rules(self):
        """Test building discipline rules prompt."""
        manager = PromptTemplateManager()
        prompt = manager.build_discipline_rules_prompt()

        assert "## 工作纪律" in prompt
        assert len(prompt) > len("## 工作纪律\n\n")

    def test_build_with_position_rules(self):
        """Test building with additional position rules."""
        manager = PromptTemplateManager()
        position_rules = ["额外的规则1", "额外的规则2"]

        prompt = manager.build_discipline_rules_prompt(position_rules=position_rules)

        assert "额外的规则1" in prompt
        assert "额外的规则2" in prompt
