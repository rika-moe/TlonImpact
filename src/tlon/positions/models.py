"""Position (Job Role) models for Tlon.

This module defines the Position data model which represents a job role
template that can be shared by multiple agents.
"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Position:
    """Position (Job Role) configuration template.
    
    A Position defines the standardized job description, responsibilities,
    and behavior for a type of role. Multiple agents can share the same position.
    
    Attributes:
        id: Unique position identifier (e.g., "developer", "project_manager")
        type: Position type code (usually same as id)
        title: Human-readable position title (e.g., "开发工程师")
        jd: Job description and responsibilities (岗位职责)
        department: Optional department name
        proactive: Whether agents in this position actively participate
        rules: List of behavior rules for this position
        default_skills: Default skills for agents in this position
        prompt_template: Optional Jinja2 template for system prompt
    
    Example:
        >>> position = Position(
        ...     id="developer",
        ...     type="developer",
        ...     title="开发工程师",
        ...     jd="负责代码编写、Bug修复和技术方案实现",
        ...     proactive=False,
        ...     rules=["只被@时响应", "可以执行bash命令"],
        ...     default_skills=["file-operations", "git-operations"],
        ... )
    """
    id: str
    type: str
    title: str = ""
    jd: str = ""  # Job Description
    department: str = ""
    proactive: bool = False
    rules: List[str] = field(default_factory=list)
    default_skills: List[str] = field(default_factory=list)
    prompt_template: Optional[str] = None
    contact_when: str = ""  # 何时应该联系该角色
    
    def to_dict(self) -> dict:
        """Convert position to dictionary.
        
        Returns:
            Dictionary representation of the position
        """
        return {
            "id": self.id,
            "type": self.type,
            "title": self.title,
            "jd": self.jd,
            "department": self.department,
            "proactive": self.proactive,
            "rules": self.rules,
            "default_skills": self.default_skills,
            "prompt_template": self.prompt_template,
            "contact_when": self.contact_when,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "Position":
        """Create position from dictionary.
        
        Args:
            data: Dictionary containing position data
            
        Returns:
            Position instance
        """
        return cls(
            id=data["id"],
            type=data.get("type", data["id"]),
            title=data.get("title", ""),
            jd=data.get("jd", data.get("description", "")),
            department=data.get("department", ""),
            proactive=data.get("proactive", False),
            rules=data.get("rules", []),
            default_skills=data.get("default_skills", data.get("skills", [])),
            prompt_template=data.get("prompt_template"),
            contact_when=data.get("contact_when", ""),
        )
    
    def get_display_name(self) -> str:
        """Get the display name for this position.
        
        Returns title if available, otherwise id.
        
        Returns:
            Display name string
        """
        return self.title or self.id
