"""Skill validation tool for Agent Skills standard.

Validates skill directories and SKILL.md files for compliance.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from .models import SkillParser


@dataclass
class ValidationIssue:
    """A validation issue."""
    
    level: str  # "error", "warning", "info"
    message: str
    path: Optional[str] = None
    
    def __str__(self) -> str:
        prefix = {
            "error": "❌",
            "warning": "⚠️",
            "info": "ℹ️",
        }.get(self.level, "❓")
        
        location = f" [{self.path}]" if self.path else ""
        return f"{prefix} {self.level.upper()}: {self.message}{location}"


@dataclass
class ValidationResult:
    """Result of skill validation."""
    
    skill_name: str
    skill_path: str
    is_valid: bool = False
    issues: List[ValidationIssue] = field(default_factory=list)
    
    def add_issue(self, level: str, message: str, path: Optional[str] = None) -> None:
        """Add a validation issue."""
        self.issues.append(ValidationIssue(level, message, path))
        if level == "error":
            self.is_valid = False
    
    def __str__(self) -> str:
        lines = [
            f"\n{'='*60}",
            f"Skill: {self.skill_name}",
            f"Path: {self.skill_path}",
            f"Status: {'✅ VALID' if self.is_valid else '❌ INVALID'}",
            f"{'='*60}",
        ]
        
        if self.issues:
            lines.append("Issues:")
            for issue in self.issues:
                lines.append(f"  {issue}")
        else:
            lines.append("No issues found!")
        
        return "\n".join(lines)


class SkillValidator:
    """Validator for Agent Skills standard compliance.
    
    Validates:
    - Directory structure
    - SKILL.md frontmatter
    - SKILL.md required sections
    - Script executability
    
    Example:
        >>> validator = SkillValidator()
        >>> result = validator.validate_skill("./skills/my-skill")
        >>> print(result)
        >>> print(f"Valid: {result.is_valid}")
    """
    
    # Required frontmatter fields
    REQUIRED_FIELDS = {"name", "description"}
    
    # Optional but recommended fields
    RECOMMENDED_FIELDS = {"metadata"}
    
    # Required sections in SKILL.md content (after frontmatter)
    # None are strictly required, but these are recommended
    RECOMMENDED_SECTIONS = [
        "when to use",
        "available scripts",
        "prerequisites",
    ]
    
    def __init__(self, skills_dir: Optional[str] = None):
        """Initialize validator.
        
        Args:
            skills_dir: Base directory for skills (optional)
        """
        self.skills_dir = Path(skills_dir) if skills_dir else None
        self.parser = SkillParser()
    
    def validate_skill(self, skill_path: str) -> ValidationResult:
        """Validate a single skill directory.
        
        Args:
            skill_path: Path to skill directory
            
        Returns:
            ValidationResult with issues found
        """
        path = Path(skill_path)
        skill_name = path.name
        
        result = ValidationResult(
            skill_name=skill_name,
            skill_path=str(path.absolute()),
            is_valid=True  # Assume valid until error found
        )
        
        # 1. Check directory exists
        if not path.exists():
            result.add_issue("error", f"Skill directory does not exist: {path}")
            return result
        
        if not path.is_dir():
            result.add_issue("error", f"Path is not a directory: {path}")
            return result
        
        # 2. Check SKILL.md exists
        skill_md = path / "SKILL.md"
        if not skill_md.exists():
            result.add_issue("error", "SKILL.md not found", "SKILL.md")
            return result
        
        if not skill_md.is_file():
            result.add_issue("error", "SKILL.md is not a file", "SKILL.md")
            return result
        
        # 3. Parse and validate frontmatter
        self._validate_frontmatter(skill_md, result)
        
        # 4. Validate content structure
        self._validate_content(skill_md, result)
        
        # 5. Validate scripts directory (if exists)
        scripts_dir = path / "scripts"
        if scripts_dir.exists():
            self._validate_scripts(scripts_dir, result)
        
        # 6. Check for references/assets directories
        self._validate_optional_dirs(path, result)
        
        return result
    
    def validate_all_skills(self, directory: Optional[str] = None) -> List[ValidationResult]:
        """Validate all skills in a directory.
        
        Args:
            directory: Directory containing skills (defaults to skills_dir)
            
        Returns:
            List of validation results
        """
        base_dir = Path(directory) if directory else self.skills_dir
        
        if not base_dir or not base_dir.exists():
            return []
        
        results = []
        
        for item in sorted(base_dir.iterdir()):
            # Skip hidden directories and files
            if item.name.startswith("."):
                continue
            
            # Skip non-directory items
            if not item.is_dir():
                continue
            
            # Skip doc directory (not a skill)
            if item.name == "doc":
                continue
            
            # Check if it has SKILL.md
            if (item / "SKILL.md").exists():
                result = self.validate_skill(str(item))
                results.append(result)
        
        return results
    
    def _validate_frontmatter(self, skill_md: Path, result: ValidationResult) -> None:
        """Validate frontmatter in SKILL.md."""
        try:
            metadata, _ = self.parser.parse_file(skill_md)
            
            # Check required fields
            for field in self.REQUIRED_FIELDS:
                if field not in metadata or not metadata[field]:
                    result.add_issue(
                        "error",
                        f"Required frontmatter field '{field}' is missing or empty",
                        "SKILL.md"
                    )
            
            # Check recommended fields
            for field in self.RECOMMENDED_FIELDS:
                if field not in metadata:
                    result.add_issue(
                        "info",
                        f"Recommended frontmatter field '{field}' is missing",
                        "SKILL.md"
                    )
            
            # Validate name format
            if "name" in metadata:
                name = metadata["name"]
                # Name should be kebab-case
                if not re.match(r'^[a-z0-9]+(-[a-z0-9]+)*$', name):
                    result.add_issue(
                        "warning",
                        f"Skill name '{name}' should be kebab-case (lowercase with hyphens)",
                        "SKILL.md"
                    )
                
                # Name should match directory name
                expected_name = skill_md.parent.name
                if name != expected_name:
                    result.add_issue(
                        "warning",
                        f"Skill name '{name}' does not match directory name '{expected_name}'",
                        "SKILL.md"
                    )
            
            # Validate description
            if "description" in metadata:
                desc = metadata["description"]
                if len(desc) < 20:
                    result.add_issue(
                        "warning",
                        f"Description is very short ({len(desc)} chars), should be more descriptive",
                        "SKILL.md"
                    )
                elif len(desc) > 300:
                    result.add_issue(
                        "info",
                        f"Description is very long ({len(desc)} chars), consider being more concise",
                        "SKILL.md"
                    )
                    
        except Exception as e:
            result.add_issue("error", f"Failed to parse frontmatter: {e}", "SKILL.md")
    
    def _validate_content(self, skill_md: Path, result: ValidationResult) -> None:
        """Validate SKILL.md content structure."""
        try:
            content = skill_md.read_text(encoding="utf-8")
            content_lower = content.lower()
            
            # Check for recommended sections
            for section in self.RECOMMENDED_SECTIONS:
                if section not in content_lower:
                    result.add_issue(
                        "info",
                        f"Recommended section '{section}' not found",
                        "SKILL.md"
                    )
            
            # Check for code blocks (usually indicate script examples)
            if "```" not in content:
                result.add_issue(
                    "info",
                    "No code blocks found - consider adding usage examples",
                    "SKILL.md"
                )
            
            # Check content length
            content_lines = [line for line in content.split("\n") if line.strip()]
            if len(content_lines) < 10:
                result.add_issue(
                    "warning",
                    f"SKILL.md is quite short ({len(content_lines)} lines of content)",
                    "SKILL.md"
                )
                
        except Exception as e:
            result.add_issue("error", f"Failed to read content: {e}", "SKILL.md")
    
    def _validate_scripts(self, scripts_dir: Path, result: ValidationResult) -> None:
        """Validate scripts directory."""
        if not scripts_dir.is_dir():
            result.add_issue("error", "scripts is not a directory", "scripts/")
            return
        
        # Get all script files
        scripts = list(scripts_dir.iterdir())
        
        if not scripts:
            result.add_issue("warning", "scripts directory is empty", "scripts/")
            return
        
        for script in scripts:
            # Skip hidden files
            if script.name.startswith("."):
                continue
            
            # Check if file is executable (for shell scripts)
            if script.suffix in {".sh", ".py"}:
                # Check shebang for scripts
                try:
                    first_line = script.read_text(encoding="utf-8").split("\n")[0]
                    if not first_line.startswith("#!/"):
                        result.add_issue(
                            "info",
                            f"Script '{script.name}' lacks shebang line",
                            f"scripts/{script.name}"
                        )
                except Exception:
                    pass
                
                # Check if executable
                import stat
                mode = script.stat().st_mode
                if not (mode & stat.S_IXUSR):
                    result.add_issue(
                        "warning",
                        f"Script '{script.name}' is not executable (chmod +x)",
                        f"scripts/{script.name}"
                    )
    
    def _validate_optional_dirs(self, skill_path: Path, result: ValidationResult) -> None:
        """Validate optional directories."""
        # references/ directory
        refs_dir = skill_path / "references"
        if refs_dir.exists():
            if not refs_dir.is_dir():
                result.add_issue("error", "references is not a directory", "references/")
            else:
                files = list(refs_dir.iterdir())
                if files:
                    result.add_issue(
                        "info",
                        f"Found {len(files)} reference file(s)",
                        "references/"
                    )
        
        # assets/ directory
        assets_dir = skill_path / "assets"
        if assets_dir.exists():
            if not assets_dir.is_dir():
                result.add_issue("error", "assets is not a directory", "assets/")
            else:
                files = list(assets_dir.iterdir())
                if files:
                    result.add_issue(
                        "info",
                        f"Found {len(files)} asset file(s)",
                        "assets/"
                    )


def main():
    """CLI entry point for skill validation."""
    import sys
    
    skills_dir = sys.argv[1] if len(sys.argv) > 1 else "./skills"
    
    validator = SkillValidator(skills_dir)
    results = validator.validate_all_skills()
    
    # Print results
    valid_count = sum(1 for r in results if r.is_valid)
    total_count = len(results)
    
    for result in results:
        print(result)
    
    # Summary
    print(f"\n{'='*60}")
    print(f"Validation Summary: {valid_count}/{total_count} skills valid")
    print(f"{'='*60}")
    
    # Exit with error code if any invalid
    sys.exit(0 if valid_count == total_count else 1)


if __name__ == "__main__":
    main()
