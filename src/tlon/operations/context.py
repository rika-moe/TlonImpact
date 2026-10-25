"""Read member-owned materials as ordinary data for program composition."""

from collections.abc import Callable
from typing import Any, Protocol

from tlon.operations.core import CallContext, Operation
from tlon.operations.schema import OperationError


class ContextReader(Protocol):
    async def build_skills_index(self) -> str: ...
    def build_lessons(self) -> str: ...
    def build_current_task(self) -> str: ...
    async def build_thread_recap(self) -> str: ...
    async def build_mail_history(self, limit: int) -> list[dict[str, Any]]: ...


def member_context_operation(
    reader: ContextReader | None = None, tools: Callable[[], list[str]] | None = None
) -> Operation:
    async def read(_context: CallContext, args: dict[str, Any]) -> dict[str, Any]:
        if reader is None or tools is None:
            raise OperationError("member context reader is not bound")
        return {
            "lessons": reader.build_lessons(),
            "skills": await reader.build_skills_index(),
            "current_task": reader.build_current_task(),
            "thread_recap": await reader.build_thread_recap(),
            "mail_history": await reader.build_mail_history(args.get("history_limit", 20)),
            "tools": tools(),
        }

    text = {"type": "string"}
    properties: dict[str, Any] = dict.fromkeys(
        ("lessons", "skills", "current_task", "thread_recap"), text
    )
    properties.update(
        {
            "mail_history": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"role": text, "content": text},
                    "required": ["role", "content"],
                    "additionalProperties": False,
                },
            },
            "tools": {"type": "array", "items": text},
        }
    )
    return Operation(
        "member-context",
        "读取自己的教训、技能索引、当前任务、最近交互、邮件历史与可用工具名；返回普通值，不解释流程配置。",
        {
            "type": "object",
            "properties": {
                "history_limit": {"type": "integer", "minimum": 0, "maximum": 100},
            },
            "required": [],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
        read,
        effects=frozenset({"read"}),
    )
