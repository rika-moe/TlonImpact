"""Tests for skill models and parser."""

import tempfile
from pathlib import Path

import pytest

from tlon.skills.models import SkillMetadata, SkillParser, Skill


class TestSkillMetadata:
    """Tests for SkillMetadata."""
    
    def test_basic_creation(self):
        """Test basic metadata creation."""
        meta = SkillMetadata(
            name="test-skill",
            description="A test skill",
            root_path="./skills/test-skill"
        )
        
        assert meta.name == "test-skill"
        assert meta.description == "A test skill"
        assert meta.root_path == "./skills/test-skill"
        assert meta.metadata == {}
    
    def test_to_table_row(self):
        """Test table row formatting."""
        meta = SkillMetadata(
            name="csv-pipeline",
            description="Process CSV files",
            root_path="./skills/csv-pipeline"
        )
        
        row = meta.to_table_row()
        assert "csv-pipeline" in row
        assert "Process CSV files" in row
        assert "./skills/csv-pipeline" in row
    
    def test_to_table_row_long_description(self):
        """Test that long descriptions are truncated."""
        meta = SkillMetadata(
            name="test",
            description="A" * 100,
            root_path="./skills/test"
        )
        
        row = meta.to_table_row()
        assert "..." in row
    
    def test_table_header_and_footer(self):
        """Test table formatting methods."""
        header = SkillMetadata.get_table_header()
        footer = SkillMetadata.get_table_footer()
        
        assert "技能名称" in header
        assert "描述" in header
        assert "┌" in header
        assert "└" in footer


class TestSkillParser:
    """Tests for SkillParser."""
    
    def test_parse_frontmatter_and_content(self):
        """Test parsing frontmatter and markdown content."""
        content = """---
name: test-skill
description: A test skill
version: 1.0
---

# Test Skill

This is the content.
"""
        
        frontmatter, markdown = SkillParser.parse_content(content)
        
        assert frontmatter["name"] == "test-skill"
        assert frontmatter["description"] == "A test skill"
        assert frontmatter["version"] == 1.0  # Parsed as number
        assert "# Test Skill" in markdown
        assert "This is the content." in markdown
    
    def test_parse_no_frontmatter(self):
        """Test parsing content without frontmatter."""
        content = """# Just Markdown

No frontmatter here.
"""
        
        frontmatter, markdown = SkillParser.parse_content(content)
        
        assert frontmatter == {}
        assert "# Just Markdown" in markdown
    
    def test_parse_yaml_types(self):
        """Test parsing different YAML value types."""
        content = """---
string_val: hello
number_val: 42
float_val: 3.14
bool_true: true
bool_false: false
null_val: null
json_val: {"key": "value"}
---

Content
"""
        
        frontmatter, _ = SkillParser.parse_content(content)
        
        assert frontmatter["string_val"] == "hello"
        assert frontmatter["number_val"] == 42
        assert frontmatter["float_val"] == 3.14
        assert frontmatter["bool_true"] is True
        assert frontmatter["bool_false"] is False
        assert frontmatter["null_val"] is None
        assert frontmatter["json_val"] == {"key": "value"}
    
    def test_parse_file(self, tmp_path):
        """Test parsing from a file."""
        skill_md = tmp_path / "SKILL.md"
        skill_md.write_text("""---
name: file-test
description: Testing file parsing
---

# File Test

Content here.
""")
        
        frontmatter, markdown = SkillParser.parse_file(skill_md)
        
        assert frontmatter["name"] == "file-test"
        assert "# File Test" in markdown
    
    def test_parse_file_not_found(self):
        """Test error when file doesn't exist."""
        with pytest.raises(FileNotFoundError):
            SkillParser.parse_file(Path("/nonexistent/SKILL.md"))
    
    def test_parse_skill_directory(self, tmp_path):
        """Test parsing a complete skill directory."""
        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()
        
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text("""---
name: my-skill
description: My test skill
metadata:
  emoji: 🧪
---

# My Skill

This is my skill content.
""")
        
        skill = SkillParser.parse_skill(skill_dir, root_path_prefix="./skills")
        
        assert skill.metadata.name == "my-skill"
        assert skill.metadata.description == "My test skill"
        assert skill.metadata.root_path == "./skills/test-skill"
        assert skill.metadata.metadata["emoji"] == "🧪"
        assert skill.is_active is False
        assert "# My Skill" in skill.content


class TestSkill:
    """Tests for Skill class."""
    
    def test_activate(self):
        """Test skill activation."""
        meta = SkillMetadata(
            name="test",
            description="Test skill",
            root_path="./skills/test"
        )
        skill = Skill(
            metadata=meta,
            content="Skill content here",
            is_active=False
        )
        
        content = skill.activate()
        
        assert content == "Skill content here"
        assert skill.is_active is True
    
    def test_deactivate(self):
        """Test skill deactivation."""
        meta = SkillMetadata(
            name="test",
            description="Test skill",
            root_path="./skills/test"
        )
        skill = Skill(
            metadata=meta,
            content="Content",
            is_active=True
        )
        
        skill.deactivate()
        
        assert skill.is_active is False
