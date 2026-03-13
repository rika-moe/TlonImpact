"""Tests for TaskManager."""

import tempfile
import shutil

from tlon.subsystem.tools.tasks import TaskManager, TaskStatus


class TestTaskManager:
    """Test TaskManager functionality."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.manager = TaskManager(agent_id="test-agent", data_dir=self.temp_dir)
    
    def teardown_method(self):
        """Clean up test fixtures."""
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_create_task(self):
        """Test creating a task."""
        task = self.manager.create_task("Test Task", "Description")
        
        assert task.id == "t-001"
        assert task.title == "Test Task"
        assert task.status == TaskStatus.TODO
        assert "t-001" in self.manager.tasks
    
    def test_create_multiple_tasks(self):
        """Test creating multiple tasks."""
        task1 = self.manager.create_task("Task 1")
        task2 = self.manager.create_task("Task 2")
        task3 = self.manager.create_task("Task 3")
        
        assert task1.id == "t-001"
        assert task2.id == "t-002"
        assert task3.id == "t-003"
    
    def test_get_task(self):
        """Test getting a task by ID."""
        created = self.manager.create_task("Test")
        retrieved = self.manager.get_task("t-001")
        
        assert retrieved is not None
        assert retrieved.id == created.id
    
    def test_get_task_not_found(self):
        """Test getting a non-existent task."""
        task = self.manager.get_task("non-existent")
        
        assert task is None
    
    def test_list_tasks(self):
        """Test listing all tasks."""
        self.manager.create_task("Task 1")
        self.manager.create_task("Task 2")
        
        tasks = self.manager.list_tasks()
        
        assert len(tasks) == 2
    
    def test_list_tasks_with_filter(self):
        """Test listing tasks with status filter."""
        self.manager.create_task("Task 1")
        task2 = self.manager.create_task("Task 2")
        self.manager.start_task(task2.id)
        
        todo_tasks = self.manager.list_tasks("todo")
        doing_tasks = self.manager.list_tasks("doing")
        
        assert len(todo_tasks) == 1
        assert len(doing_tasks) == 1
    
    def test_start_task(self):
        """Test starting a task."""
        task = self.manager.create_task("Test")
        
        started, paused = self.manager.start_task(task.id)
        
        assert started.status == TaskStatus.DOING
        assert paused is None  # No previous task to pause
    
    def test_start_task_auto_pause(self):
        """Test that starting a task auto-pauses current task."""
        task1 = self.manager.create_task("Task 1")
        task2 = self.manager.create_task("Task 2")
        
        self.manager.start_task(task1.id)
        started, paused = self.manager.start_task(task2.id)
        
        assert started.id == task2.id
        assert paused.id == task1.id
        assert self.manager.get_task(task1.id).status == TaskStatus.PAUSED
    
    def test_pause_task(self):
        """Test pausing a task."""
        task = self.manager.create_task("Test")
        self.manager.start_task(task.id)
        
        paused = self.manager.pause_task(task.id)
        
        assert paused.status == TaskStatus.PAUSED
        assert paused.pause_count == 1
    
    def test_complete_task(self):
        """Test completing a task."""
        task = self.manager.create_task("Test")
        self.manager.start_task(task.id)
        
        completed = self.manager.complete_task(task.id, "Result!")
        
        assert completed.status == TaskStatus.DONE
        assert completed.result == "Result!"
    
    def test_remove_task(self):
        """Test removing a completed task."""
        task = self.manager.create_task("Test")
        self.manager.start_task(task.id)
        self.manager.complete_task(task.id)
        
        removed = self.manager.remove_task(task.id)
        
        assert removed is not None
        assert task.id not in self.manager.tasks
    
    def test_remove_task_not_done(self):
        """Test that non-done tasks cannot be removed."""
        task = self.manager.create_task("Test")
        
        removed = self.manager.remove_task(task.id)
        
        assert removed is None
        assert task.id in self.manager.tasks
    
    def test_get_current_task_doing(self):
        """Test getting current task when one is doing."""
        task = self.manager.create_task("Test")
        self.manager.start_task(task.id)
        
        current = self.manager.get_current_task()
        
        assert current is not None
        assert current.id == task.id
    
    def test_get_current_task_paused(self):
        """Test getting current task when none doing but has paused."""
        task = self.manager.create_task("Test")
        self.manager.start_task(task.id)
        self.manager.pause_task(task.id)
        
        current = self.manager.get_current_task()
        
        assert current is not None
        assert current.id == task.id
    
    def test_get_stats(self):
        """Test getting task statistics."""
        self.manager.create_task("Task 1")
        task2 = self.manager.create_task("Task 2")
        self.manager.start_task(task2.id)
        
        stats = self.manager.get_stats()
        
        assert stats["total"] == 2
        assert stats["todo"] == 1
        assert stats["doing"] == 1
        assert stats["paused"] == 0
        assert stats["done"] == 0
    
    def test_save_and_load(self):
        """Test persisting and loading tasks."""
        # Create and modify tasks
        task = self.manager.create_task("Test")
        self.manager.start_task(task.id)
        
        # Save
        self.manager.save()
        
        # Create new manager and load
        new_manager = TaskManager(agent_id="test-agent", data_dir=self.temp_dir)
        new_manager.load()
        
        # Verify
        assert len(new_manager.tasks) == 1
        loaded = new_manager.get_task("t-001")
        assert loaded.title == "Test"
        assert loaded.status == TaskStatus.DOING
    
    def test_clear(self):
        """Test clearing all tasks."""
        self.manager.create_task("Task 1")
        self.manager.create_task("Task 2")
        
        self.manager.clear()
        
        assert len(self.manager.tasks) == 0
        assert self.manager._counter == 0