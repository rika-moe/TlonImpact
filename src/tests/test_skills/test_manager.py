"""Tests for SkillManager."""

import pytest

from tlon.subsystem.skills.manager import SkillManager
from tlon.subsystem.skills.models import SkillMetadata


class TestSkillManagerDiscovery:
    """Tests for skill discovery phase."""
    
    def test_discover_skills_empty_dir(self, tmp_path):
        """Test discovery with empty directory."""
        manager = SkillManager(str(tmp_path))
        skills = manager.discover_skills()
        
        assert skills == []
    
    def test_discover_skills(self, tmp_path):
        """Test discovering skills."""
        # Create test skill directories
        skill1 = tmp_path / "test-skill-1"
        skill1.mkdir()
        (skill1 / "SKILL.md").write_text("""---
name: test-skill-1
description: First test skill
---

# Skill 1
""")
        
        skill2 = tmp_path / "test-skill-2"
        skill2.mkdir()
        (skill2 / "SKILL.md").write_text("""---
name: test-skill-2
description: Second test skill
---

# Skill 2
""")
        
        manager = SkillManager(str(tmp_path))
        skills = manager.discover_skills()
        
        assert len(skills) == 2
        names = {s.name for s in skills}
        assert names == {"test-skill-1", "test-skill-2"}
    
    def test_discover_skills_filter(self, tmp_path):
        """Test discovering specific skills."""
        # Create test skills
        for name in ["skill-a", "skill-b", "skill-c"]:
            skill_dir = tmp_path / name
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(f"""---
name: {name}
description: Test
---

# {name}
""")
        
        manager = SkillManager(str(tmp_path))
        skills = manager.discover_skills(skill_names=["skill-a", "skill-c"])
        
        assert len(skills) == 2
        names = {s.name for s in skills}
        assert names == {"skill-a", "skill-c"}
    
    def test_discover_skips_hidden(self, tmp_path):
        """Test that hidden directories are skipped."""
        hidden = tmp_path / ".hidden"
        hidden.mkdir()
        (hidden / "SKILL.md").write_text("---\nname: hidden\n---\n\nHidden")
        
        manager = SkillManager(str(tmp_path))
        skills = manager.discover_skills()
        
        assert len(skills) == 0
    
    def test_discover_skips_no_skill_md(self, tmp_path):
        """Test that directories without SKILL.md are skipped."""
        no_skill = tmp_path / "no-skill"
        no_skill.mkdir()
        (no_skill / "README.md").write_text("Not a skill")
        
        manager = SkillManager(str(tmp_path))
        skills = manager.discover_skills()
        
        assert len(skills) == 0


class TestSkillManagerActivation:
    """Tests for skill activation phase."""
    
    @pytest.fixture
    def manager_with_skills(self, tmp_path):
        """Create a manager with test skills."""
        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("""---
name: test-skill
description: A test skill
---

# Test Skill

This is the skill content.
""")
        
        manager = SkillManager(str(tmp_path))
        manager.discover_skills()
        return manager
    
    def test_activate_skill(self, manager_with_skills):
        """Test activating a skill."""
        content = manager_with_skills.activate_skill("test-skill")
        
        assert "# Test Skill" in content
        assert "This is the skill content." in content
        assert manager_with_skills.is_active("test-skill")
    
    def test_activate_nonexistent_skill(self, manager_with_skills):
        """Test activating a skill that doesn't exist."""
        content = manager_with_skills.activate_skill("nonexistent")
        
        assert content is None
    
    def test_activate_already_active(self, manager_with_skills):
        """Test activating an already active skill."""
        content1 = manager_with_skills.activate_skill("test-skill")
        content2 = manager_with_skills.activate_skill("test-skill")
        
        assert content1 == content2
        assert manager_with_skills.is_active("test-skill")
    
    def test_deactivate_skill(self, manager_with_skills):
        """Test deactivating a skill."""
        manager_with_skills.activate_skill("test-skill")
        assert manager_with_skills.is_active("test-skill")
        
        result = manager_with_skills.deactivate_skill("test-skill")
        
        assert result is True
        assert not manager_with_skills.is_active("test-skill")
    
    def test_deactivate_not_active(self, manager_with_skills):
        """Test deactivating a skill that isn't active."""
        result = manager_with_skills.deactivate_skill("test-skill")
        
        assert result is False
    
    def test_deactivate_nonexistent(self, manager_with_skills):
        """Test deactivating a skill that doesn't exist."""
        result = manager_with_skills.deactivate_skill("nonexistent")
        
        assert result is False
    
    def test_get_active_skills(self, manager_with_skills):
        """Test getting list of active skills."""
        assert manager_with_skills.get_active_skills() == []
        
        manager_with_skills.activate_skill("test-skill")
        
        assert manager_with_skills.get_active_skills() == ["test-skill"]


class TestSkillManagerResourceLoading:
    """Tests for skill resource loading (Execution phase)."""
    
    @pytest.fixture
    def manager_with_resources(self, tmp_path):
        """Create a manager with a skill that has resources."""
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
        (refs_dir / "guide.md").write_text("# Guide\n\nReference content.")
        
        assets_dir = skill_dir / "assets"
        assets_dir.mkdir()
        (assets_dir / "template.txt").write_text("Template content")
        
        manager = SkillManager(str(tmp_path))
        manager.discover_skills()
        return manager
    
    def test_load_resource_references(self, manager_with_resources):
        """Test loading a reference file."""
        content = manager_with_resources.load_resource("test-skill", "references/guide.md")
        
        assert "# Guide" in content
        assert "Reference content." in content
    
    def test_load_resource_assets(self, manager_with_resources):
        """Test loading an asset file."""
        content = manager_with_resources.load_resource("test-skill", "assets/template.txt")
        
        assert content == "Template content"
    
    def test_load_resource_skill_not_found(self, manager_with_resources):
        """Test loading from non-existent skill."""
        with pytest.raises(FileNotFoundError, match="Skill not found"):
            manager_with_resources.load_resource("nonexistent", "file.txt")
    
    def test_load_resource_file_not_found(self, manager_with_resources):
        """Test loading non-existent resource."""
        with pytest.raises(FileNotFoundError, match="Resource not found"):
            manager_with_resources.load_resource("test-skill", "references/missing.md")
    
    def test_load_resource_directory_traversal(self, manager_with_resources):
        """Test that directory traversal attacks are prevented."""
        with pytest.raises(ValueError, match="Invalid resource path"):
            manager_with_resources.load_resource("test-skill", "../../../etc/passwd")
    
    def test_load_resource_not_a_file(self, manager_with_resources):
        """Test loading a directory as a resource."""
        with pytest.raises(ValueError, match="Resource is not a file"):
            manager_with_resources.load_resource("test-skill", "references")


class TestSkillManagerPromptIntegration:
    """Tests for prompt integration features."""
    
    def test_get_skills_table_for_prompt(self, tmp_path):
        """Test generating skills table for system prompt."""
        # Create test skills
        for name in ["skill-a", "skill-b"]:
            skill_dir = tmp_path / name
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(f"""---
name: {name}
description: Description for {name}
---

# {name}
""")
        
        manager = SkillManager(str(tmp_path))
        manager.discover_skills()
        
        table = manager.get_skills_table_for_prompt()
        
        # Check table structure
        assert "技能名称" in table
        assert "描述" in table
        assert "根路径" in table
        assert "skill-a" in table
        assert "skill-b" in table
        
        # Check instructions are included
        assert "activateSkill" in table
        assert "loadSkillResource" in table
        assert "deactivateSkill" in table
    
    def test_get_skills_table_empty(self, tmp_path):
        """Test generating table when no skills exist."""
        manager = SkillManager(str(tmp_path))
        manager.discover_skills()
        
        table = manager.get_skills_table_for_prompt()
        
        assert "暂无可用技能" in table
    
    def test_get_active_skills_context(self, tmp_path):
        """Test getting combined context of active skills."""
        # Create test skills
        for name in ["skill-a", "skill-b"]:
            skill_dir = tmp_path / name
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(f"""---
name: {name}
description: Test
---

# {name} Content

Content for {name}.
""")
        
        manager = SkillManager(str(tmp_path))
        manager.discover_skills()
        
        # No active skills
        assert manager.get_active_skills_context() == ""
        
        # Activate skills
        manager.activate_skill("skill-a")
        manager.activate_skill("skill-b")
        
        context = manager.get_active_skills_context()
        
        assert "## Skill: skill-a" in context
        assert "## Skill: skill-b" in context
        assert "# skill-a Content" in context
        assert "# skill-b Content" in context
    
    def test_get_metadata(self, tmp_path):
        """Test getting skill metadata."""
        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("""---
name: test-skill
description: Test description
---

# Test
""")
        
        manager = SkillManager(str(tmp_path))
        manager.discover_skills()
        
        meta = manager.get_metadata("test-skill")
        
        assert isinstance(meta, SkillMetadata)
        assert meta.name == "test-skill"
        assert meta.description == "Test description"
    
    def test_get_metadata_not_found(self, tmp_path):
        """Test getting metadata for non-existent skill."""
        manager = SkillManager(str(tmp_path))
        
        meta = manager.get_metadata("nonexistent")
        
        assert meta is None
    
    def test_get_skill(self, tmp_path):
        """Test getting full skill object."""
        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("""---
name: test-skill
description: Test
---

# Content
""")
        
        manager = SkillManager(str(tmp_path))
        manager.discover_skills()
        
        from tlon.subsystem.skills.models import Skill
        skill = manager.get_skill("test-skill")
        
        assert isinstance(skill, Skill)
        assert skill.metadata.name == "test-skill"
