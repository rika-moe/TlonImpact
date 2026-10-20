"""Member-owned persona and contact contracts shared by DSL and Tool."""

from typing import Any

from tlon.base.paths import TlonPaths
from tlon.identity.contacts import ContactService
from tlon.identity.persona import PersonaService
from tlon.operations.core import CallContext, Operation
from tlon.operations.schema import InputError, OperationError, UnknownOutcomeError


def _object(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties) if required is None else required,
        "additionalProperties": False,
    }


_STRING = {"type": "string"}


def make_identity_operations(tlon_paths: TlonPaths | None, member_id: str) -> tuple[Operation, ...]:
    """Bind private paths once; callers cannot choose another owner."""

    async def execute(
        service: str, context: CallContext, args: dict[str, Any], *, write: bool = False
    ) -> dict[str, Any]:
        if tlon_paths is None:
            raise OperationError("identity service is not bound")
        if context.member_id != member_id:
            raise InputError("identity service belongs to another member")
        try:
            if service == "read":
                return await ContactService(tlon_paths, member_id).read()
            if service == "upsert":
                return await ContactService(tlon_paths, member_id).upsert(**args)
            return await PersonaService(tlon_paths, member_id).update(**args)
        except InputError:
            raise
        except Exception as error:
            if not write:
                raise
            raise UnknownOutcomeError(
                str(error), {"outcome": "unknown", "call_id": context.call_id, "retry_safe": False}
            ) from error

    async def read(context: CallContext, args: dict[str, Any]) -> dict[str, Any]:
        return await execute("read", context, args)

    async def upsert(context: CallContext, args: dict[str, Any]) -> dict[str, Any]:
        return await execute("upsert", context, args, write=True)

    async def persona(context: CallContext, args: dict[str, Any]) -> dict[str, Any]:
        return await execute("persona", context, args, write=True)

    return (
        Operation(
            "contacts-read",
            "读取自己的私有联系簿全文，包含自由文本与条目数。",
            _object({}),
            _object({"content": _STRING, "entries": {"type": "integer"}, "message": _STRING}),
            read,
            effects=frozenset({"read"}),
        ),
        Operation(
            "contacts-upsert",
            "按名字添加或更新自己的联系簿条目，保留其他自由文本；description为空则删除。修改在下次组装生效。",
            _object(
                {
                    "name": {"type": "string", "minLength": 1},
                    "address": _STRING,
                    "description": _STRING,
                },
                ["name", "description"],
            ),
            _object(
                {
                    "name": _STRING,
                    "address": _STRING,
                    "action": {"type": "string", "enum": ["added", "updated", "deleted"]},
                    "message": _STRING,
                },
                ["name", "action", "message"],
            ),
            upsert,
            effects=frozenset({"write"}),
        ),
        Operation(
            "persona-update",
            "全文替换自己的IDENTITY或SOUL；RULES仅人类可改。修改下次组装生效，记录内容哈希；记账失败仍报告已写入及警告。",
            _object(
                {
                    "file": {"type": "string", "enum": ["identity", "soul"]},
                    "content": {"type": "string", "minLength": 1},
                }
            ),
            _object(
                {"file": _STRING, "path": _STRING, "bytes": {"type": "integer"}, "message": _STRING}
            ),
            persona,
            effects=frozenset({"write"}),
        ),
    )


def identity_operation_definitions() -> tuple[Operation, ...]:
    """Inspect contracts without touching member files."""
    return make_identity_operations(None, "")
