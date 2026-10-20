"""Task Manager for agent personal task management."""

import json
import os
import tempfile
from copy import deepcopy

from tlon.base.paths import TlonPaths

from .models import Task, TaskStatus


class TaskManager:
    """Manages tasks for a single agent.

    Features:
    - Create, read, update, delete tasks
    - Track task status transitions
    - Auto-increment task IDs
    - Persistent storage to JSONL file
    """

    def __init__(self, agent_id: str, tlon_paths: TlonPaths) -> None:
        """Initialize TaskManager.

        Args:
            agent_id: Unique identifier for the agent
            tlon_paths: Path resolver; the tasks file resolves as
                ``{home}/{agent_id}/tasks.jsonl``
        """
        self.agent_id = agent_id
        self.tasks: dict[str, Task] = {}
        self._counter = 0
        self._needs_reload = False
        self._storage_path = tlon_paths.agent_tasks_file(agent_id)

    def _ensure_current(self) -> None:
        if self._needs_reload:
            self.load()

    def _commit(self, tasks: dict[str, Task], counter: int) -> None:
        """Persist a private candidate before publishing any in-memory changes."""
        lines = [json.dumps(task.to_dict(), ensure_ascii=False) for task in tasks.values()]
        try:
            self._write_lines(lines)
        except BaseException:
            # A failure may be reported after replace committed. Reload actual
            # disk state rather than pretending the candidate was rolled back.
            self._needs_reload = True
            self.load()
            raise
        self.tasks = tasks
        self._counter = counter

    def create_task(self, title: str, description: str = "") -> Task:
        self._ensure_current()
        counter = self._counter + 1
        task = Task(id=f"t-{counter:03d}", title=title, description=description)
        tasks = deepcopy(self.tasks)
        tasks[task.id] = task
        self._commit(tasks, counter)
        return deepcopy(task)

    def get_task(self, task_id: str) -> Task | None:
        """Get a task by ID.

        Args:
            task_id: Task identifier

        Returns:
            Task object or None if not found
        """
        self._ensure_current()
        return deepcopy(self.tasks.get(task_id))

    def list_tasks(self, status_filter: str | None = None) -> list[Task]:
        """List all tasks, optionally filtered by status.

        Args:
            status_filter: Filter by status ("todo", "doing", "paused", "done")

        Returns:
            List of Task objects
        """
        self._ensure_current()
        tasks = deepcopy(list(self.tasks.values()))

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
        self._ensure_current()
        # First, look for doing task
        for task in self.tasks.values():
            if task.status == TaskStatus.DOING:
                return deepcopy(task)

        # Then, look for most recently paused task
        paused_tasks = [t for t in self.tasks.values() if t.status == TaskStatus.PAUSED]
        if paused_tasks:
            # Sort by paused_at, most recent first
            paused_tasks.sort(key=lambda t: t.paused_at or "", reverse=True)
            return deepcopy(paused_tasks[0])

        return None

    def start_task(self, task_id: str) -> tuple[Task | None, Task | None]:
        self._ensure_current()
        tasks = deepcopy(self.tasks)
        task = tasks.get(task_id)
        if task is None:
            return None, None
        paused = None
        for current in tasks.values():
            if current.status == TaskStatus.DOING:
                current.pause()
                paused = current
                break
        task.start()
        self._commit(tasks, self._counter)
        return deepcopy(task), deepcopy(paused)

    def pause_task(self, task_id: str, reason: str = "") -> Task | None:
        self._ensure_current()
        tasks = deepcopy(self.tasks)
        task = tasks.get(task_id)
        if task is None:
            return None
        if task.status == TaskStatus.DOING:
            task.pause()
            if reason:
                task.description = f"{task.description}\n[Paused] {reason}".strip()
            self._commit(tasks, self._counter)
        return deepcopy(task)

    def complete_task(self, task_id: str, result: str = "") -> Task | None:
        self._ensure_current()
        tasks = deepcopy(self.tasks)
        task = tasks.get(task_id)
        if task is None:
            return None
        task.complete(result)
        self._commit(tasks, self._counter)
        return deepcopy(task)

    def remove_task(self, task_id: str) -> Task | None:
        self._ensure_current()
        task = self.tasks.get(task_id)
        if task is None or task.status != TaskStatus.DONE:
            return None
        tasks = deepcopy(self.tasks)
        del tasks[task_id]
        self._commit(tasks, self._counter)
        return deepcopy(task)

    def get_stats(self) -> dict[str, int]:
        """Get task statistics.

        Returns:
            Dict with counts by status
        """
        self._ensure_current()
        return {
            "total": len(self.tasks),
            "todo": sum(1 for t in self.tasks.values() if t.status == TaskStatus.TODO),
            "doing": sum(1 for t in self.tasks.values() if t.status == TaskStatus.DOING),
            "paused": sum(1 for t in self.tasks.values() if t.status == TaskStatus.PAUSED),
            "done": sum(1 for t in self.tasks.values() if t.status == TaskStatus.DONE),
        }

    def save(self) -> None:
        """Persist the current task snapshot."""
        self._ensure_current()
        self._commit(deepcopy(self.tasks), self._counter)

    def _write_lines(self, lines: list[str]) -> None:
        """Replace the complete task file atomically; persistence failures propagate."""
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=self._storage_path.parent, prefix=".tasks-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write("\n".join(lines) + ("\n" if lines else ""))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._storage_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def load(self) -> None:
        """Publish only a complete successfully read committed snapshot."""
        self._needs_reload = True
        tasks: dict[str, Task] = {}
        counter = 0
        if self._storage_path.exists():
            for line in self._read_lines():
                task = Task.from_dict(json.loads(line))
                tasks[task.id] = task
                counter = max(counter, int(task.id.split("-")[1]))
        self.tasks = tasks
        self._counter = counter
        self._needs_reload = False

    def _read_lines(self) -> list[str]:
        """Read non-empty stripped lines from the storage file (blocking IO)."""
        with open(self._storage_path, encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]

    def clear(self) -> None:
        """Persist an empty snapshot before discarding the current one."""
        self._ensure_current()
        self._commit({}, 0)
