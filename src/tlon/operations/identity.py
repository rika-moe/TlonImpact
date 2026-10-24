"""Member-owned persona contract shared by DSL and Tool."""

from typing import Any

from tlon.base.paths import TlonPaths
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

    async def persona(context: CallContext, args: dict[str, Any]) -> dict[str, Any]:
        if tlon_paths is None:
            raise OperationError("identity service is not bound")
        if context.member_id != member_id:
            raise InputError("identity service belongs to another member")
        try:
            return await PersonaService(tlon_paths, member_id).update(**args)
        except InputError:
            raise
        except Exception as error:
            raise UnknownOutcomeError(
                str(error), {"outcome": "unknown", "call_id": context.call_id, "retry_safe": False}
            ) from error

    return (
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
