"""Tests for prompt template manager."""

import tempfile
from pathlib import Path

import yaml

from tlon.base.config import PromptTemplateManager, ConfigLoader, AgentConfig, PositionConfig, PromptOverrides


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
                    "prompt_overrides": {
                        "workflow": "developer specific workflow"
                    }
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


class TestBuildTeamContextPrompt:
    """Test building team context prompt section."""
    
    def test_build_team_context(self):
        """Test building team context."""
        manager = PromptTemplateManager()
        team_members = [
            ("林知遥", "开发工程师", "负责后端开发", "需要开发时"),
            ("沈清晏", "项目经理", "负责项目管理", "需要协调时"),
        ]
        
        prompt = manager.build_team_context_prompt(team_members)
        
        assert "## 你的团队" in prompt
        assert "林知遥" in prompt
        assert "沈清晏" in prompt
        assert "需要开发时" in prompt
    
    def test_build_empty_team(self):
        """Test building with empty team."""
        manager = PromptTemplateManager()
        prompt = manager.build_team_context_prompt([])
        
        assert prompt == ""


class TestBuildRolePrompt:
    """Test building role prompt section."""
    
    def test_build_role_prompt(self):
        """Test building role prompt."""
        manager = PromptTemplateManager()
        position = PositionConfig(
            id="dev",
            type="developer",
            title="开发",
            jd="负责代码开发和维护",
        )
        
        prompt = manager.build_role_prompt(position)
        
        assert "## 岗位职责" in prompt
        assert "负责代码开发和维护" in prompt
    
    def test_build_role_prompt_no_jd(self):
        """Test building role prompt without JD."""
        manager = PromptTemplateManager()
        position = PositionConfig(
            id="dev",
            type="developer",
            title="开发",
            jd="",
        )
        
        prompt = manager.build_role_prompt(position)
        
        assert prompt == ""
    
    def test_build_role_prompt_no_position(self):
        """Test building role prompt without position."""
        manager = PromptTemplateManager()
        prompt = manager.build_role_prompt(None)
        
        assert prompt == ""


class TestBuildSystemPrompt:
    """Test building complete system prompt."""
    
    def test_build_system_prompt(self):
        """Test building complete system prompt."""
        manager = PromptTemplateManager()
        agent = AgentConfig(
            id="dev-001",
            name="林知遥",
            position_id="developer",
            personality="细心、负责",
        )
        position = PositionConfig(
            id="developer",
            type="developer",
            title="开发工程师",
            jd="负责代码开发",
            rules=["代码必须经过测试"],
        )
        
        prompt = manager.build_system_prompt(agent, position)
        
        assert "林知遥" in prompt
        assert "开发工程师" in prompt
        assert "细心、负责" in prompt
        assert "## 工作流程" in prompt
        assert "## 重要提示" in prompt
        assert "## 工作纪律" in prompt
    
    def test_build_without_position(self):
        """Test building without position uses agent.position_id."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a position file
            position_data = {
                "position": {
                    "type": "developer",
                    "title": "开发工程师",
                    "jd": "负责开发",
                }
            }
            with open(Path(tmpdir) / "developer.yaml", "w") as f:
                yaml.dump(position_data, f)
            
            loader = ConfigLoader(positions_dir=tmpdir)
            manager = PromptTemplateManager(loader)
            
            agent = AgentConfig(
                id="dev-001",
                name="林知遥",
                position_id="developer",
            )
            
            prompt = manager.build_system_prompt(agent)
            
            assert "林知遥" in prompt
            assert "开发工程师" in prompt


class TestReloadTemplates:
    """Test reloading templates."""
    
    def test_reload_clears_cache(self):
        """Test reload clears all caches."""
        manager = PromptTemplateManager()
        
        # Load templates to cache
        manager.load_global_templates()
        assert manager._global_templates is not None
        
        # Reload should clear cache
        manager.reload_templates()
        assert manager._global_templates is None
