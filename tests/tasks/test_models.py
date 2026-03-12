"""Tests for Task models."""

import pytest
from datetime import datetime
from tlon.tasks import Task, TaskStatus


class TestTask:
    """Test Task model."""
    
    def test_task_creation(self):
        """Test basic task creation."""
        task = Task(
            id="t-001",
            title="Test Task",
            description="Test description"
        )
        
        assert task.id == "t-001"
        assert task.title == "Test Task"
        assert task.description == "Test description"
        assert task.status == TaskStatus.TODO
        assert task.pause_count == 0
    
    def test_task_start(self):
        """Test starting a task."""
        task = Task(id="t-001", title="Test")
        
        task.start()
        
        assert task.status == TaskStatus.DOING
        assert task.started_at is not None
    
    def test_task_pause(self):
        """Test pausing a task."""
        task = Task(id="t-001", title="Test")
        task.start()
        
        task.pause()
        
        assert task.status == TaskStatus.PAUSED
        assert task.paused_at is not None
        assert task.pause_count == 1
    
    def test_task_resume(self):
        """Test resuming a paused task."""
        task = Task(id="t-001", title="Test")
        task.start()
        task.pause()
        
        task.start()  # Resume
        
        assert task.status == TaskStatus.DOING
        assert task.paused_at is None
        assert task.pause_count == 1  # Count doesn't decrease
    
    def test_task_complete(self):
        """Test completing a task."""
        task = Task(id="t-001", title="Test")
        task.start()
        
        task.complete(result="Done!")
        
        assert task.status == TaskStatus.DONE
        assert task.completed_at is not None
        assert task.result == "Done!"
    
    def test_task_get_duration(self):
        """Test duration calculation."""
        task = Task(id="t-001", title="Test")
        task.start()
        task.complete()
        
        duration = task.get_duration_minutes()
        
        assert duration is not None
        assert duration >= 0
    
    def test_task_to_dict(self):
        """Test task serialization."""
        task = Task(id="t-001", title="Test")
        
        data = task.to_dict()
        
        assert data["id"] == "t-001"
        assert data["title"] == "Test"
        assert data["status"] == "todo"  # Enum converted to string
    
    def test_task_from_dict(self):
        """Test task deserialization."""
        data = {
            "id": "t-001",
            "title": "Test",
            "status": "doing",
            "description": "",
            "created_at": datetime.now().isoformat(),
            "started_at": None,
            "paused_at": None,
            "completed_at": None,
            "result": "",
            "pause_count": 0
        }
        
        task = Task.from_dict(data)
        
        assert task.id == "t-001"
        assert task.status == TaskStatus.DOING  # String converted to enum


class TestTaskStatus:
    """Test TaskStatus enum."""
    
    def test_status_values(self):
        """Test status enum values."""
        assert TaskStatus.TODO.value == "todo"
        assert TaskStatus.DOING.value == "doing"
        assert TaskStatus.PAUSED.value == "paused"
        assert TaskStatus.DONE.value == "done"