"""Task Manager for agent personal task management."""

import json

from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths

from .models import Task, TaskStatus

logger = get_logger(__name__)


class TaskManager:
    """Manages tasks for a single agent.

    Features:
    - Create, read, update, delete tasks
    - Track task status transitions
    - Auto-increment task IDs
    - Persistent storage to JSONL file
    """

    def __init__(self, agent_id: str, tlon_paths: TlonPaths):
        """Initialize TaskManager.

        Args:
            agent_id: Unique identifier for the agent
            tlon_paths: Path resolver; the tasks file resolves as
                ``{home}/{agent_id}/tasks.jsonl``
        """
        self.agent_id = agent_id
        self.tasks: dict[str, Task] = {}
        self._counter = 0
        self._storage_path = tlon_paths.agent_tasks_file(agent_id)

    def _generate_id(self) -> str:
        """Generate a new task ID.

        Returns:
            Task ID in format "t-001", "t-002", etc.
        """
        self._counter += 1
        return f"t-{self._counter:03d}"

    def create_task(self, title: str, description: str = "") -> Task:
        """Create a new task.

        Args:
            title: Task title
            description: Optional task description

        Returns:
            The created Task object
        """
        task_id = self._generate_id()
        task = Task(
            id=task_id,
            title=title,
            description=description,
            status=TaskStatus.TODO,
        )
        self.tasks[task_id] = task
        self.save()
        return task

    def get_task(self, task_id: str) -> Task | None:
        """Get a task by ID.

        Args:
            task_id: Task identifier

        Returns:
            Task object or None if not found
        """
        return self.tasks.get(task_id)

    def list_tasks(self, status_filter: str | None = None) -> list[Task]:
        """List all tasks, optionally filtered by status.

        Args:
            status_filter: Filter by status ("todo", "doing", "paused", "done")

        Returns:
            List of Task objects
        """
        tasks = list(self.tasks.values())

        if status_filter:
            tasks = [t for t in tasks if t.status.value == status_filter]

        # Sort by creation time
        tasks.sort(key=lambda t: t.created_at)
        return tasks

    def get_current_task(self) -> Task | None:
        """Get the currently active task.

        Returns:
            The doing task, or the most recently paused task,
            or None if no active task exists.
        """
        # First, look for doing task
        for task in self.tasks.values():
            if task.status == TaskStatus.DOING:
                return task

        # Then, look for most recently paused task
        paused_tasks = [t for t in self.tasks.values() if t.status == TaskStatus.PAUSED]
        if paused_tasks:
            # Sort by paused_at, most recent first
            paused_tasks.sort(key=lambda t: t.paused_at or "", reverse=True)
            return paused_tasks[0]

        return None

    def start_task(self, task_id: str) -> tuple[Task | None, Task | None]:
        """Start a task.

        If there's a currently doing task, it will be paused automatically.

        Args:
            task_id: ID of task to start

        Returns:
            Tuple of (started_task, paused_task)
            paused_task is the previously doing task that was paused, or None
        """
        task = self.tasks.get(task_id)
        if not task:
            return None, None

        # Pause current doing task if exists
        paused_task = None
        for t in self.tasks.values():
            if t.status == TaskStatus.DOING:
                t.pause()
                paused_task = t
                break

        # Start the target task
        task.start()
        self.save()

        return task, paused_task

    def pause_task(self, task_id: str) -> Task | None:
        """Pause a task.

        Args:
            task_id: ID of task to pause

        Returns:
            The paused task, or None if not found
        """
        task = self.tasks.get(task_id)
        if not task:
            return None

        if task.status == TaskStatus.DOING:
            task.pause()
            self.save()

        return task

    def complete_task(self, task_id: str, result: str = "") -> Task | None:
        """Mark a task as completed.

        Args:
            task_id: ID of task to complete
            result: Optional result summary

        Returns:
            The completed task, or None if not found
        """
        task = self.tasks.get(task_id)
        if not task:
            return None

        task.complete(result)
        self.save()
        return task

    def remove_task(self, task_id: str) -> Task | None:
        """Remove a task from the list.

        Only done tasks can be removed.

        Args:
            task_id: ID of task to remove

        Returns:
            The removed task, or None if not found or not done
        """
        task = self.tasks.get(task_id)
        if not task:
            return None

        if task.status != TaskStatus.DONE:
            return None

        del self.tasks[task_id]
        self.save()
        return task

    def get_stats(self) -> dict[str, int]:
        """Get task statistics.

        Returns:
            Dict with counts by status
        """
        return {
            "total": len(self.tasks),
            "todo": sum(1 for t in self.tasks.values() if t.status == TaskStatus.TODO),
            "doing": sum(1 for t in self.tasks.values() if t.status == TaskStatus.DOING),
            "paused": sum(1 for t in self.tasks.values() if t.status == TaskStatus.PAUSED),
            "done": sum(1 for t in self.tasks.values() if t.status == TaskStatus.DONE),
        }

    def save(self) -> None:
        """Save all tasks to JSONL file.

        Note: the file IO is blocking but isolated in _write_lines(). The
        public mutation API is synchronous (called from sync tool code and
        tests), so save() stays sync; the per-agent task file is small enough
        that the blocking cost is negligible. If the API is ever made async,
        only _write_lines() needs to be offloaded to a thread.
        """
        try:
            lines = [json.dumps(task.to_dict(), ensure_ascii=False) for task in self.tasks.values()]
            self._write_lines(lines)
        except Exception as e:
            # Log error but don't crash
            logger.warning("task_save_failed", error=str(e))

    def _write_lines(self, lines: list[str]) -> None:
        """Write lines to the storage file (blocking IO, isolated for save())."""
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._storage_path, "w", encoding="utf-8") as f:
            for line in lines:
                f.write(line + "\n")

    def load(self) -> None:
        """Load tasks from JSONL file.

        Note: same blocking-IO caveat as save(); the IO is isolated in
        _read_lines().
        """
        if not self._storage_path.exists():
            return

        try:
            self.tasks = {}
            max_counter = 0

            for line in self._read_lines():
                data = json.loads(line)
                task = Task.from_dict(data)
                self.tasks[task.id] = task

                # Update counter from loaded tasks
                try:
                    counter = int(task.id.split("-")[1])
                    max_counter = max(max_counter, counter)
                except (IndexError, ValueError):
                    pass

            self._counter = max_counter

        except Exception as e:
            # Log error and start fresh
            logger.warning("task_load_failed", error=str(e))
            self.tasks = {}
            self._counter = 0

    def _read_lines(self) -> list[str]:
        """Read non-empty stripped lines from the storage file (blocking IO)."""
        with open(self._storage_path, encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]

    def clear(self) -> None:
        """Clear all tasks (for testing)."""
        self.tasks = {}
        self._counter = 0
        self.save()
