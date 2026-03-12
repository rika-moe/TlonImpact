"""Tests for skill-related tools."""

import pytest

from tlon.skills.manager import SkillManager
from tlon.tools.activate_skill import ActivateSkillTool
from tlon.tools.deactivate_skill import DeactivateSkillTool
from tlon.tools.load_skill_resource import LoadSkillResourceTool


class TestActivateSkillTool:
    """Tests for ActivateSkillTool."""
    
    @pytest.fixture
    def tool_with_skills(self, tmp_path):
        """Create a tool with test skills."""
        # Create test skill
        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("""---
name: test-skill
description: A test skill for testing
---

# Test Skill

This is the test skill content.
""")
        
        manager = SkillManager(str(tmp_path))
        manager.discover_skills()
        
        return ActivateSkillTool(manager)
    
    async def test_activate_existing_skill(self, tool_with_skills):
        """Test activating an existing skill."""
        result = await tool_with_skills.execute(skillName="test-skill")
        
        assert result.success is True
        assert result.data["skill_name"] == "test-skill"
        assert "# Test Skill" in result.data["content"]
        assert result.data["content_length"] > 0
    
    async def test_activate_nonexistent_skill(self, tool_with_skills):
        """Test activating a non-existent skill."""
        result = await tool_with_skills.execute(skillName="nonexistent")
        
        assert result.success is False
        assert "not found" in result.error.lower()
    
    def test_tool_definition(self, tool_with_skills):
        """Test tool definition for OpenAI."""
        definition = tool_with_skills.to_openai_definition()
        
        assert definition["type"] == "function"
        assert definition["function"]["name"] == "activateSkill"
        assert "skillName" in definition["function"]["parameters"]["properties"]


class TestLoadSkillResourceTool:
    """Tests for LoadSkillResourceTool."""
    
    @pytest.fixture
    def tool_with_resources(self, tmp_path):
        """Create a tool with test resources."""
        # Create test skill with resources
        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("""---
name: test-skill
description: Test
---

# Test
""")
        
        # Create resources
        refs_dir = skill_dir / "references"
        refs_dir.mkdir()
        (refs_dir / "guide.md").write_text("# Guide\n\nGuide content here.")
        
        manager = SkillManager(str(tmp_path))
        manager.discover_skills()
        
        return LoadSkillResourceTool(manager)
    
    async def test_load_existing_resource(self, tool_with_resources):
        """Test loading an existing resource."""
        result = await tool_with_resources.execute(
            skillName="test-skill",
            resourceName="references/guide.md"
        )
        
        assert result.success is True
        assert result.data["skill_name"] == "test-skill"
        assert result.data["resource_name"] == "references/guide.md"
        assert "# Guide" in result.data["content"]
    
    async def test_load_nonexistent_resource(self, tool_with_resources):
        """Test loading a non-existent resource."""
        result = await tool_with_resources.execute(
            skillName="test-skill",
            resourceName="references/missing.md"
        )
        
        assert result.success is False
        assert "not found" in result.error.lower() or "Resource not found" in result.error
    
    async def test_load_from_nonexistent_skill(self, tool_with_resources):
        """Test loading from a non-existent skill."""
        result = await tool_with_resources.execute(
            skillName="nonexistent",
            resourceName="file.txt"
        )
        
        assert result.success is False
        assert "not found" in result.error.lower() or "Skill not found" in result.error


class TestDeactivateSkillTool:
    """Tests for DeactivateSkillTool."""
    
    @pytest.fixture
    def tool_with_active_skill(self, tmp_path):
        """Create a tool with an active skill."""
        # Create test skill
        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("""---
name: test-skill
description: Test
---

# Test
""")
        
        manager = SkillManager(str(tmp_path))
        manager.discover_skills()
        manager.activate_skill("test-skill")
        
        return DeactivateSkillTool(manager)
    
    async def test_deactivate_active_skill(self, tool_with_active_skill):
        """Test deactivating an active skill."""
        result = await tool_with_active_skill.execute(skillName="test-skill")
        
        assert result.success is True
        assert result.data["skill_name"] == "test-skill"
        assert result.data["status"] == "deactivated"
    
    async def test_deactivate_inactive_skill(self, tool_with_active_skill):
        """Test deactivating an inactive skill."""
        # First deactivate
        await tool_with_active_skill.execute(skillName="test-skill")
        
        # Try again
        result = await tool_with_active_skill.execute(skillName="test-skill")
        
        assert result.success is False
        assert "not currently active" in result.error.lower()
    
    async def test_deactivate_nonexistent_skill(self, tool_with_active_skill):
        """Test deactivating a non-existent skill."""
        result = await tool_with_active_skill.execute(skillName="nonexistent")
        
        assert result.success is False
