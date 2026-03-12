"""Integration tests for Agent Skills system.

Phase 2.6: 集成测试
- Skill Discovery 测试
- Skill Activation 测试
- Skill Execution 测试
- 多 Skill 同时加载测试
"""

import json
import subprocess
from pathlib import Path

import pytest

from tlon.skills.manager import SkillManager
from tlon.tools.activate_skill import ActivateSkillTool
from tlon.tools.deactivate_skill import DeactivateSkillTool
from tlon.tools.load_skill_resource import LoadSkillResourceTool

# =============================================================================
# Phase 2.6.1: Skill Discovery 集成测试
# =============================================================================

class TestSkillDiscoveryIntegration:
    """Integration tests for skill discovery with real skills directory."""
    
    @pytest.fixture
    def real_skills_dir(self):
        """Get the real skills directory path."""
        return Path(__file__).parent.parent.parent / "skills"
    
    def test_discover_all_real_skills(self, real_skills_dir):
        """Test discovering all skills in the actual skills directory."""
        if not real_skills_dir.exists():
            pytest.skip("Real skills directory not found")
        
        manager = SkillManager(str(real_skills_dir))
        skills = manager.discover_skills()
        
        # Should discover at least the 5 standard skills
        assert len(skills) >= 5
        
        skill_names = {s.name for s in skills}
        expected_skills = {
            "csv-pipeline",
            "plan-i",
            "frontend-design", 
            "file-operations",
            "git-operations"
        }
        
        for expected in expected_skills:
            assert expected in skill_names, f"Expected skill '{expected}' not found"
    
    def test_discover_specific_skills(self, real_skills_dir):
        """Test discovering specific skills by name."""
        if not real_skills_dir.exists():
            pytest.skip("Real skills directory not found")
        
        manager = SkillManager(str(real_skills_dir))
        skills = manager.discover_skills(skill_names=["file-operations", "git-operations"])
        
        assert len(skills) == 2
        
        skill_names = {s.name for s in skills}
        assert "file-operations" in skill_names
        assert "git-operations" in skill_names
        assert "csv-pipeline" not in skill_names
    
    def test_skill_metadata_parsing(self, real_skills_dir):
        """Test that skill metadata is correctly parsed from real skills."""
        if not real_skills_dir.exists():
            pytest.skip("Real skills directory not found")
        
        manager = SkillManager(str(real_skills_dir))
        skills = manager.discover_skills()
        
        # Find file-operations skill
        file_ops = next((s for s in skills if s.name == "file-operations"), None)
        assert file_ops is not None

        print(file_ops)
        print(file_ops.metadata)

        # Check metadata
        assert file_ops.name == "file-operations"
        assert "file" in file_ops.description.lower() or "文件" in file_ops.description
        assert "file" in file_ops.metadata["tags"]
        assert "filesystem" in file_ops.metadata["tags"]

    def test_skills_table_generation(self, real_skills_dir):
        """Test generating skills table from real skills."""
        if not real_skills_dir.exists():
            pytest.skip("Real skills directory not found")
        
        manager = SkillManager(str(real_skills_dir))
        manager.discover_skills()
        
        table = manager.get_skills_table_for_prompt()
        
        # Check table contains expected content
        assert "技能名称" in table
        assert "file-operations" in table
        assert "git-operations" in table
        assert "csv-pipeline" in table
        assert "activateSkill" in table


# =============================================================================
# Phase 2.6.2: Skill Activation 集成测试
# =============================================================================

class TestSkillActivationIntegration:
    """Integration tests for skill activation workflow."""
    
    @pytest.fixture
    def manager_with_real_skills(self, tmp_path):
        """Create a manager with test skills simulating real ones."""
        # Create a skill similar to file-operations
        skill_dir = tmp_path / "test-file-ops"
        skill_dir.mkdir()
        
        # Create SKILL.md with frontmatter and content
        (skill_dir / "SKILL.md").write_text("""---
name: test-file-ops
description: Test file operations skill
metadata: {"category": "file-system", "tags": ["files", "test"]}
---

# File Operations

## When to Use

Use this skill for file operations testing.

## Available Scripts

### read_file.py

```bash
python3 /skills/test-file-ops/scripts/read_file.py --path <file>
```

Reads a file and returns its contents.
""")
        
        # Create scripts directory with a test script
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir()
        
        script_content = '''#!/usr/bin/env python3
import argparse
import json

parser = argparse.ArgumentParser()
parser.add_argument("--path", required=True)
args = parser.parse_args()

print(json.dumps({"success": True, "data": {"path": args.path}}))
'''
        (scripts_dir / "read_file.py").write_text(script_content)
        (scripts_dir / "read_file.py").chmod(0o755)
        
        manager = SkillManager(str(tmp_path))
        manager.discover_skills()
        return manager
    
    def test_activate_single_skill(self, manager_with_real_skills):
        """Test activating a single skill."""
        content = manager_with_real_skills.activate_skill("test-file-ops")
        
        assert content is not None
        assert "# File Operations" in content
        assert "## When to Use" in content
        assert "## Available Scripts" in content
        assert "read_file.py" in content
        
        # Verify skill is marked as active
        assert manager_with_real_skills.is_active("test-file-ops")
    
    def test_activate_already_active_skill(self, manager_with_real_skills):
        """Test activating a skill that's already active."""
        # Activate first time
        content1 = manager_with_real_skills.activate_skill("test-file-ops")
        
        # Activate second time - should return same content
        content2 = manager_with_real_skills.activate_skill("test-file-ops")
        
        assert content1 == content2
        assert manager_with_real_skills.is_active("test-file-ops")
    
    def test_activate_nonexistent_skill(self, manager_with_real_skills):
        """Test activating a skill that doesn't exist."""
        content = manager_with_real_skills.activate_skill("nonexistent-skill")
        
        assert content is None
        assert not manager_with_real_skills.is_active("nonexistent-skill")
    
    def test_deactivate_active_skill(self, manager_with_real_skills):
        """Test deactivating an active skill."""
        # Activate then deactivate
        manager_with_real_skills.activate_skill("test-file-ops")
        assert manager_with_real_skills.is_active("test-file-ops")
        
        result = manager_with_real_skills.deactivate_skill("test-file-ops")
        
        assert result is True
        assert not manager_with_real_skills.is_active("test-file-ops")
    
    def test_deactivate_inactive_skill(self, manager_with_real_skills):
        """Test deactivating a skill that was never activated."""
        result = manager_with_real_skills.deactivate_skill("test-file-ops")
        
        assert result is False
    
    def test_get_active_skills_list(self, manager_with_real_skills):
        """Test getting list of active skills."""
        # Initially no active skills
        assert manager_with_real_skills.get_active_skills() == []
        
        # Activate skill
        manager_with_real_skills.activate_skill("test-file-ops")
        
        # Check active skills list
        active = manager_with_real_skills.get_active_skills()
        assert "test-file-ops" in active


# =============================================================================
# Phase 2.6.3: Skill Execution 集成测试
# =============================================================================

class TestSkillExecutionIntegration:
    """Integration tests for skill script execution."""
    
    @pytest.fixture
    def temp_skill_with_scripts(self, tmp_path):
        """Create a temporary skill with executable scripts."""
        skill_dir = tmp_path / "test-exec"
        skill_dir.mkdir()
        
        # Create SKILL.md
        (skill_dir / "SKILL.md").write_text("""---
name: test-exec
description: Test execution skill
---

# Test Execution

## Available Scripts

### test_echo.sh

Echo test script.

### test_math.py

Math operations script.
""")
        
        # Create scripts directory
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir()
        
        # Bash script
        bash_script = '''#!/bin/bash
if [ -z "$1" ]; then
    echo '{"success": false, "error": "No message provided"}'
    exit 1
fi
MESSAGE="$1"
printf '{"success": true, "data": {"message": "%s"}}' "$MESSAGE"
'''
        (scripts_dir / "test_echo.sh").write_text(bash_script)
        (scripts_dir / "test_echo.sh").chmod(0o755)
        
        # Python script
        python_script = '''#!/usr/bin/env python3
import argparse
import json

parser = argparse.ArgumentParser()
parser.add_argument("--a", type=int, required=True)
parser.add_argument("--b", type=int, required=True)
parser.add_argument("--op", choices=["add", "mul"], default="add")
args = parser.parse_args()

if args.op == "add":
    result = args.a + args.b
else:
    result = args.a * args.b

print(json.dumps({"success": True, "data": {"result": result}}))
'''
        (scripts_dir / "test_math.py").write_text(python_script)
        (scripts_dir / "test_math.py").chmod(0o755)
        
        return skill_dir
    
    def test_bash_script_execution(self, temp_skill_with_scripts):
        """Test executing a bash script from skill."""
        script_path = temp_skill_with_scripts / "scripts" / "test_echo.sh"
        
        result = subprocess.run(
            ["bash", str(script_path), "hello"],
            capture_output=True,
            text=True
        )
        
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["success"] is True
        assert output["data"]["message"] == "hello"
    
    def test_python_script_execution(self, temp_skill_with_scripts):
        """Test executing a Python script from skill."""
        script_path = temp_skill_with_scripts / "scripts" / "test_math.py"
        
        result = subprocess.run(
            ["python3", str(script_path), "--a", "5", "--b", "3", "--op", "add"],
            capture_output=True,
            text=True
        )
        
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["success"] is True
        assert output["data"]["result"] == 8
    
    def test_python_script_multiplication(self, temp_skill_with_scripts):
        """Test Python script with multiplication operation."""
        script_path = temp_skill_with_scripts / "scripts" / "test_math.py"
        
        result = subprocess.run(
            ["python3", str(script_path), "--a", "5", "--b", "3", "--op", "mul"],
            capture_output=True,
            text=True
        )
        
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["data"]["result"] == 15
    
    def test_script_error_handling(self, temp_skill_with_scripts):
        """Test script error handling."""
        script_path = temp_skill_with_scripts / "scripts" / "test_echo.sh"
        
        result = subprocess.run(
            ["bash", str(script_path)],  # No argument - should error
            capture_output=True,
            text=True
        )
        
        assert result.returncode == 1
        output = json.loads(result.stdout)
        assert output["success"] is False
        assert "error" in output
    
    def test_skill_resource_loading(self, temp_skill_with_scripts):
        """Test loading skill resources during execution."""
        manager = SkillManager(str(temp_skill_with_scripts.parent))
        manager.discover_skills()
        
        # Load the skill content
        content = manager.activate_skill("test-exec")
        
        assert "test_echo.sh" in content
        assert "test_math.py" in content


# =============================================================================
# Phase 2.6.4: 多 Skill 同时加载测试
# =============================================================================

class TestMultipleSkillsLoading:
    """Integration tests for multiple skills loading simultaneously."""
    
    @pytest.fixture
    def multi_skill_manager(self, tmp_path):
        """Create a manager with multiple skills."""
        skills = [
            {
                "name": "skill-alpha",
                "description": "Alpha skill for testing",
                "content": "# Alpha Skill\n\nAlpha content here."
            },
            {
                "name": "skill-beta", 
                "description": "Beta skill for testing",
                "content": "# Beta Skill\n\nBeta content here."
            },
            {
                "name": "skill-gamma",
                "description": "Gamma skill for testing",
                "content": "# Gamma Skill\n\nGamma content here."
            }
        ]
        
        for skill_data in skills:
            skill_dir = tmp_path / skill_data["name"]
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(f"""---
name: {skill_data['name']}
description: {skill_data['description']}
---

{skill_data['content']}
""")
        
        manager = SkillManager(str(tmp_path))
        manager.discover_skills()
        return manager
    
    def test_activate_multiple_skills(self, multi_skill_manager):
        """Test activating multiple skills."""
        # Activate all three skills
        for name in ["skill-alpha", "skill-beta", "skill-gamma"]:
            content = multi_skill_manager.activate_skill(name)
            assert content is not None
        
        # Check all are active
        active = multi_skill_manager.get_active_skills()
        assert len(active) == 3
        assert "skill-alpha" in active
        assert "skill-beta" in active
        assert "skill-gamma" in active
    
    def test_get_combined_active_skills_context(self, multi_skill_manager):
        """Test getting combined context of multiple active skills."""
        # Activate multiple skills
        multi_skill_manager.activate_skill("skill-alpha")
        multi_skill_manager.activate_skill("skill-beta")
        
        # Get combined context
        context = multi_skill_manager.get_active_skills_context()
        
        # Should contain both skills
        assert "## Skill: skill-alpha" in context
        assert "## Skill: skill-beta" in context
        assert "# Alpha Skill" in context
        assert "# Beta Skill" in context
    
    def test_activate_then_deactivate_some(self, multi_skill_manager):
        """Test activating multiple then deactivating some."""
        # Activate all
        for name in ["skill-alpha", "skill-beta", "skill-gamma"]:
            multi_skill_manager.activate_skill(name)
        
        # Deactivate one
        result = multi_skill_manager.deactivate_skill("skill-beta")
        assert result is True
        
        # Check remaining active skills
        active = multi_skill_manager.get_active_skills()
        assert len(active) == 2
        assert "skill-alpha" in active
        assert "skill-beta" not in active
        assert "skill-gamma" in active
    
    def test_activate_same_skill_multiple_times(self, multi_skill_manager):
        """Test that activating same skill multiple times doesn't duplicate."""
        # Activate same skill 3 times
        for _ in range(3):
            multi_skill_manager.activate_skill("skill-alpha")
        
        # Should only appear once in active list
        active = multi_skill_manager.get_active_skills()
        assert active.count("skill-alpha") == 1
        assert len(active) == 1
    
    def test_skills_isolation(self, multi_skill_manager):
        """Test that skills are isolated from each other."""
        # Activate two skills
        content_alpha = multi_skill_manager.activate_skill("skill-alpha")
        content_beta = multi_skill_manager.activate_skill("skill-beta")
        
        # Contents should be different
        assert content_alpha != content_beta
        assert "Alpha" in content_alpha
        assert "Beta" in content_beta
        assert "Alpha" not in content_beta
        assert "Beta" not in content_alpha


# =============================================================================
# Tools Integration Tests
# =============================================================================

class TestSkillToolsIntegration:
    """Integration tests for skill tools (activate, deactivate, load)."""
    
    @pytest.fixture
    def tools_setup(self, tmp_path):
        """Setup for tools tests."""
        # Create test skill
        skill_dir = tmp_path / "tools-test"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("""---
name: tools-test
description: Test skill for tools
---

# Tools Test

Test content.
""")
        
        # Create resources
        refs_dir = skill_dir / "references"
        refs_dir.mkdir()
        (refs_dir / "guide.md").write_text("# Guide\n\nGuide content.")
        
        manager = SkillManager(str(tmp_path))
        manager.discover_skills()
        
        return manager
    
    async def test_activate_skill_tool(self, tools_setup):
        """Test ActivateSkillTool with real manager."""
        tool = ActivateSkillTool(tools_setup)
        
        result = await tool.execute(skillName="tools-test")
        
        assert result.success is True
        assert tools_setup.is_active("tools-test")
    
    async def test_activate_nonexistent_skill_tool(self, tools_setup):
        """Test ActivateSkillTool with nonexistent skill."""
        tool = ActivateSkillTool(tools_setup)
        
        result = await tool.execute(skillName="nonexistent")
        
        assert result.success is False
    
    async def test_deactivate_skill_tool(self, tools_setup):
        """Test DeactivateSkillTool."""
        # First activate
        tools_setup.activate_skill("tools-test")
        
        tool = DeactivateSkillTool(tools_setup)
        result = await tool.execute(skillName="tools-test")
        
        assert result.success is True
        assert not tools_setup.is_active("tools-test")
    
    async def test_load_resource_tool(self, tools_setup):
        """Test LoadSkillResourceTool."""
        tool = LoadSkillResourceTool(tools_setup)
        
        result = await tool.execute(skillName="tools-test", resourceName="references/guide.md")
        
        assert result.success is True
        assert "# Guide" in result.data["content"]
    
    async def test_load_resource_tool_not_found(self, tools_setup):
        """Test LoadSkillResourceTool with missing resource."""
        tool = LoadSkillResourceTool(tools_setup)
        
        result = await tool.execute(skillName="tools-test", resourceName="missing.txt")
        
        assert result.success is False
