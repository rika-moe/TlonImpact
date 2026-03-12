"""Task data models for task planning."""

from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional


class TaskStatus(str, Enum):
    """Task status enumeration."""
    
    TODO = "todo"           # 待处理
    DOING = "doing"         # 进行中
    PAUSED = "paused"       # 已暂停
    DONE = "done"           # 已完成


@dataclass
class Task:
    """Task model representing a unit of work.
    
    Attributes:
        id: Unique task identifier (e.g., "t-001")
        title: Task title/summary
        description: Detailed task description
        status: Current task status
        created_at: Creation timestamp (ISO format)
        started_at: When task was started
        paused_at: When task was paused
        completed_at: When task was completed
        result: Task result/output summary
        pause_count: Number of times task was paused
    """
    
    id: str
    title: str
    description: str = ""
    status: TaskStatus = TaskStatus.TODO
    
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    started_at: Optional[str] = None
    paused_at: Optional[str] = None
    completed_at: Optional[str] = None
    
    result: str = ""
    pause_count: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert task to dictionary."""
        data = asdict(self)
        # Convert enum to string
        data["status"] = self.status.value
        return data
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Task":
        """Create task from dictionary."""
        # Convert string to enum
        if "status" in data and isinstance(data["status"], str):
            data["status"] = TaskStatus(data["status"])
        return cls(**data)
    
    def start(self) -> None:
        """Mark task as started (doing)."""
        if self.status == TaskStatus.TODO:
            self.started_at = datetime.now().isoformat()
        elif self.status == TaskStatus.PAUSED:
            # Resuming from paused
            pass
        self.status = TaskStatus.DOING
        self.paused_at = None
    
    def pause(self) -> None:
        """Mark task as paused."""
        if self.status == TaskStatus.DOING:
            self.status = TaskStatus.PAUSED
            self.paused_at = datetime.now().isoformat()
            self.pause_count += 1
    
    def complete(self, result: str = "") -> None:
        """Mark task as completed.
        
        Args:
            result: Optional task result/output
        """
        self.status = TaskStatus.DONE
        self.completed_at = datetime.now().isoformat()
        if result:
            self.result = result
    
    def get_duration_minutes(self) -> Optional[int]:
        """Calculate task duration in minutes.
        
        Returns:
            Duration in minutes, or None if task not completed
        """
        if not self.completed_at or not self.started_at:
            return None
        
        start = datetime.fromisoformat(self.started_at)
        end = datetime.fromisoformat(self.completed_at)
        return int((end - start).total_seconds() / 60)
    
    def __str__(self) -> str:
        """String representation."""
        status_emoji = {
            TaskStatus.TODO: "⏳",
            TaskStatus.DOING: "🔄",
            TaskStatus.PAUSED: "⏸️",
            TaskStatus.DONE: "✅",
        }.get(self.status, "❓")
        
        return f"[{status_emoji}] {self.id}: {self.title}"
    
    def __hash__(self) -> int:
        """Hash based on ID."""
        return hash(self.id)
    
    def __eq__(self, other: object) -> bool:
        """Equality based on ID."""
        if not isinstance(other, Task):
            return NotImplemented
        return self.id == other.id