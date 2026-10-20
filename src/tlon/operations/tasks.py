"""Shared contracts for member-private task management."""

from typing import Any

from tlon.operations.core import CallContext, Operation
from tlon.operations.schema import OperationError, RejectedError, UnknownOutcomeError
from tlon.tasks.manager import TaskManager
from tlon.tasks.models import TaskStatus


def _object(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties) if required is None else required,
        "additionalProperties": False,
    }


_TEXT = {"type": "string"}
_TASK = _object(
    {
        "id": _TEXT,
        "title": _TEXT,
        "description": _TEXT,
        "status": {"type": "string", "enum": ["todo", "doing", "paused", "done"]},
        "created_at": _TEXT,
        "started_at": {"type": ["string", "null"]},
        "paused_at": {"type": ["string", "null"]},
        "completed_at": {"type": ["string", "null"]},
        "result": _TEXT,
        "pause_count": {"type": "integer"},
    }
)


def make_task_operations(manager: TaskManager | None) -> tuple[Operation, ...]:
    """Bind all task access to one host-selected member manager."""
    specs = (
        (
            "create",
            "创建自己的任务。",
            _object({"title": {"type": "string", "minLength": 1}, "description": _TEXT}, ["title"]),
        ),
        (
            "list",
            "列出自己的任务，可按状态筛选。",
            _object(
                {"filter": {"type": "string", "enum": ["all", "todo", "doing", "paused", "done"]}},
                [],
            ),
        ),
        ("start", "开始或恢复任务，自动暂停其他进行中的任务。", _object({"task_id": _TEXT})),
        (
            "pause",
            "暂停正在进行的任务并记录原因。",
            _object({"task_id": _TEXT, "reason": _TEXT}, ["task_id"]),
        ),
        (
            "complete",
            "完成已开始的任务并记录结果。",
            _object({"task_id": _TEXT, "result": _TEXT}, ["task_id"]),
        ),
        ("remove", "移除已经完成的任务。", _object({"task_id": _TEXT})),
        ("current", "读取当前进行中或最近暂停的任务。", _object({})),
    )

    def bind(action: str) -> Any:
        async def handle(context: CallContext, args: dict[str, Any]) -> dict[str, Any]:
            if manager is None:
                raise OperationError("task service is not bound")
            if context.member_id != manager.agent_id:
                raise OperationError("task manager belongs to another member")

            def reject(message: str) -> None:
                raise RejectedError(message, {"outcome": "rejected"})

            if action == "list":
                tasks = manager.list_tasks(
                    None if args.get("filter", "all") == "all" else args["filter"]
                )
                return {"tasks": [task.to_dict() for task in tasks], "stats": manager.get_stats()}
            if action == "current":
                current = manager.get_current_task()
                return {"task": current.to_dict() if current else None}
            task = manager.get_task(args["task_id"]) if action != "create" else None
            if action != "create" and task is None:
                reject(f"Task not found: {args['task_id']}")
            if task is not None:
                if action == "start" and task.status in (TaskStatus.DONE, TaskStatus.DOING):
                    reject(f"Cannot start task with status '{task.status.value}'")
                if action == "pause" and task.status != TaskStatus.DOING:
                    reject("Only 'doing' tasks can be paused")
                if action == "complete" and task.status not in (
                    TaskStatus.DOING,
                    TaskStatus.PAUSED,
                ):
                    reject("Cannot complete task that hasn't been started or is already completed")
                if action == "remove" and task.status != TaskStatus.DONE:
                    reject("Only completed tasks can be removed")
            try:
                paused = None
                if action == "create":
                    task = manager.create_task(args["title"], args.get("description", ""))
                elif action == "start":
                    task, paused = manager.start_task(args["task_id"])
                elif action == "pause":
                    task = manager.pause_task(args["task_id"], args.get("reason", ""))
                elif action == "complete":
                    task = manager.complete_task(args["task_id"], args.get("result", ""))
                elif action == "remove":
                    task = manager.remove_task(args["task_id"])
                if task is None:
                    raise RuntimeError("task mutation returned no task")
                return {"task": task.to_dict(), "auto_paused": paused.to_dict() if paused else None}
            except Exception as error:
                raise UnknownOutcomeError(
                    str(error),
                    {"outcome": "unknown", "call_id": context.call_id, "retry_safe": False},
                ) from error

        return handle

    result = []
    for action, description, inputs in specs:
        if action == "list":
            output = _object(
                {
                    "tasks": {"type": "array", "items": _TASK},
                    "stats": _object(
                        {
                            name: {"type": "integer"}
                            for name in ("total", "todo", "doing", "paused", "done")
                        }
                    ),
                }
            )
        elif action == "current":
            output = _object({"task": {**_TASK, "type": ["object", "null"]}})
        else:
            output = _object({"task": _TASK, "auto_paused": {**_TASK, "type": ["object", "null"]}})
        result.append(
            Operation(
                f"task-{action}",
                description,
                inputs,
                output,
                bind(action),
                effects=frozenset({"read" if action in ("list", "current") else "write"}),
            )
        )
    return tuple(result)


def task_operation_definitions() -> tuple[Operation, ...]:
    """Static inspection uses the same schema authority, without opening files."""
    return make_task_operations(None)
