"""One file contract shared by DSL and model calls."""

from typing import Any

from tlon.operations.core import CallContext, Operation
from tlon.sandbox.filesystem import MemberFilesystemService


def _object(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties) if required is None else required,
        "additionalProperties": False,
    }


_STRING = {"type": "string"}
_INT = {"type": "integer"}
_BOOL = {"type": "boolean"}
_PATH = {"type": "string", "minLength": 1}
_MESSAGE = {"message": _STRING}
_MATCH = {
    "type": ["string", "object"],
    "properties": {"file": _STRING, "line": _INT, "text": _STRING},
    "required": ["file", "line", "text"],
    "additionalProperties": False,
}


def make_filesystem_operations(service: MemberFilesystemService) -> tuple[Operation, ...]:
    """Bind host-owned device authority to the five definitions."""

    async def read(context: CallContext, arguments: dict[str, Any]) -> dict[str, Any]:
        return await service.read(arguments, context)

    async def write(context: CallContext, arguments: dict[str, Any]) -> dict[str, Any]:
        return await service.write(arguments, context)

    async def edit(context: CallContext, arguments: dict[str, Any]) -> dict[str, Any]:
        return await service.edit(arguments, context)

    async def listing(context: CallContext, arguments: dict[str, Any]) -> dict[str, Any]:
        return await service.list_directory(arguments, context)

    async def search(context: CallContext, arguments: dict[str, Any]) -> dict[str, Any]:
        return await service.search(arguments, context)

    return (
        Operation(
            "file-read",
            "读取自己的workspace或授权装置UTF-8文件；workspace无需Docker。offset默认1，limit默认100，最多500行。/tlon-home只读。",
            _object(
                {
                    "file_path": _PATH,
                    "offset": {"type": "integer", "minimum": 1},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                },
                ["file_path"],
            ),
            _object(
                {
                    "file_path": _STRING,
                    "content": _STRING,
                    "total_lines": _INT,
                    "read_lines": _INT,
                    "offset": _INT,
                    **_MESSAGE,
                }
            ),
            read,
            effects=frozenset({"read"}),
        ),
        Operation(
            "file-write",
            "创建或覆盖自己的workspace或授权装置文件，自动创建父目录。结果未知不可自动重试。",
            _object({"file_path": _PATH, "content": _STRING}),
            _object(
                {"file_path": _STRING, "bytes_written": _INT, "existed_before": _BOOL, **_MESSAGE}
            ),
            write,
            effects=frozenset({"write"}),
        ),
        Operation(
            "file-edit",
            "唯一匹配old_string后替换；零个或多个匹配拒绝。结果未知不可自动重试。",
            _object(
                {
                    "file_path": _PATH,
                    "old_string": {"type": "string", "minLength": 1},
                    "new_string": _STRING,
                }
            ),
            _object(
                {
                    "file_path": _STRING,
                    "replaced": _BOOL,
                    "old_length": _INT,
                    "new_length": _INT,
                    **_MESSAGE,
                }
            ),
            edit,
            effects=frozenset({"write"}),
        ),
        Operation(
            "file-list",
            "列目录；目录优先再按名称排序。path默认.，max_entries默认200、最多1000。",
            _object(
                {"path": _PATH, "max_entries": {"type": "integer", "minimum": 1, "maximum": 1000}},
                [],
            ),
            _object(
                {
                    "path": _STRING,
                    "entries": {
                        "type": "array",
                        "items": _object(
                            {
                                "name": _STRING,
                                "type": {"type": "string", "enum": ["dir", "file", "symlink"]},
                                "size": _INT,
                                "modified": _STRING,
                            }
                        ),
                    },
                    "count": _INT,
                    "truncated": _BOOL,
                    "hint": _STRING,
                    **_MESSAGE,
                },
                ["path", "entries", "count", "truncated", "message"],
            ),
            listing,
            effects=frozenset({"read"}),
        ),
        Operation(
            "file-search",
            "按内容正则或filename glob搜索；忽略.git和二进制内容。path默认.，mode默认content，max_results默认50、最多200。",
            _object(
                {
                    "pattern": _STRING,
                    "path": _PATH,
                    "mode": {"type": "string", "enum": ["content", "filename"]},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 200},
                },
                ["pattern"],
            ),
            _object(
                {
                    "mode": {"type": "string", "enum": ["content", "filename"]},
                    "pattern": _STRING,
                    "path": _STRING,
                    "matches": {"type": "array", "items": _MATCH},
                    "count": _INT,
                    "truncated": _BOOL,
                    "hint": _STRING,
                    **_MESSAGE,
                },
                ["mode", "pattern", "path", "matches", "count", "truncated", "message"],
            ),
            search,
            effects=frozenset({"read"}),
        ),
    )


def filesystem_operation_definitions() -> tuple[Operation, ...]:
    """Inspect definitions without any device or filesystem effects."""
    from tlon.base.config import DockerRuntimeConfig

    return make_filesystem_operations(MemberFilesystemService(DockerRuntimeConfig(), None, [], []))
