"""Single contracts for member lifecycle and self scheduling operations."""

from collections.abc import Awaitable
from typing import Any, TypeVar

from tlon.base.execution import BudgetExceededError, ExecutionDeadlineError
from tlon.lifecycle.service import LifecycleService
from tlon.operations.core import CallContext, Operation
from tlon.operations.schema import OperationError, UnknownOutcomeError


def _object(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties) if required is None else required,
        "additionalProperties": False,
    }


_T = TypeVar("_T")

_STRING = {"type": "string"}
_REASON = {"reason": _STRING}
_MEMBER = _object({"id": _STRING, "name": _STRING, "address": _STRING})
_DECISION = _object(
    {
        "action": {"type": "string", "enum": ["start", "stop"]},
        "agent_id": _STRING,
        "reason": _STRING,
    }
)


def make_lifecycle_operations(service: LifecycleService | None) -> tuple[Operation, ...]:
    """Bind one member's registry ports; definitions remain inspectable without runtime."""

    def bound(context: CallContext) -> LifecycleService:
        if service is None:
            raise OperationError("member lifecycle service is not bound")
        service.check_context(context)
        return service

    async def mutate(context: CallContext, pending: Awaitable[_T]) -> _T:
        try:
            return await pending
        except (OperationError, BudgetExceededError, ExecutionDeadlineError):
            raise
        except Exception as error:
            raise UnknownOutcomeError(
                str(error),
                {
                    "outcome": "unknown",
                    "call_id": context.call_id,
                    "retry_safe": False,
                },
            ) from error

    async def spawn(context: CallContext, args: dict[str, Any]) -> dict[str, Any]:
        return await mutate(context, bound(context).spawn(args, context))

    async def members(context: CallContext, args: dict[str, Any]) -> list[dict[str, str]]:
        return await bound(context).members()

    async def self_state(context: CallContext, args: dict[str, Any]) -> dict[str, Any]:
        return await bound(context).self_state()

    async def start(context: CallContext, args: dict[str, Any]) -> dict[str, Any]:
        return await mutate(
            context,
            bound(context).request("start", args["agent_id"], args.get("reason", ""), context),
        )

    async def stop(context: CallContext, args: dict[str, Any]) -> dict[str, Any]:
        return await mutate(
            context,
            bound(context).request("stop", args["agent_id"], args.get("reason", ""), context),
        )

    async def start_all(context: CallContext, args: dict[str, Any]) -> list[dict[str, Any]]:
        return await mutate(context, bound(context).request_all(args.get("reason", ""), context))

    def finish(context: CallContext, args: dict[str, Any]) -> dict[str, Any]:
        return {"finished": True}

    return (
        Operation(
            "member-spawn",
            "从可信原型创建并启动成员；受注册表预算限制。",
            _object(
                {
                    "prototype": {"type": "string", "minLength": 1},
                    "name": {"type": "string", "minLength": 1},
                    "params": {
                        "type": "object",
                        "properties": {},
                        "required": [],
                        "additionalProperties": True,
                    },
                },
                ["prototype", "name"],
            ),
            _object(
                {
                    "agent_id": _STRING,
                    "name": _STRING,
                    "prototype": _STRING,
                    "spawn_depth": {"type": "integer"},
                }
            ),
            spawn,
            effects=frozenset({"write"}),
        ),
        Operation(
            "member-list",
            "查询公开成员寻址黄页，仅包含id、名字与邮件地址。",
            _object({}),
            {"type": "array", "items": _MEMBER},
            members,
            effects=frozenset({"read"}),
        ),
        Operation(
            "member-self-state",
            "查看自己的调度状态与唤醒时间。",
            _object({}),
            {"type": "object", "properties": {}, "required": [], "additionalProperties": True},
            self_state,
            effects=frozenset({"read"}),
        ),
        Operation(
            "member-start",
            "请求启动成员；普通成员只能管理自己，adam可代管。",
            _object({"agent_id": _STRING, **_REASON}, ["agent_id"]),
            _DECISION,
            start,
            effects=frozenset({"write"}),
        ),
        Operation(
            "member-stop",
            "请求停止成员；普通成员只能管理自己，adam可代管。",
            _object({"agent_id": _STRING, **_REASON}, ["agent_id"]),
            _DECISION,
            stop,
            effects=frozenset({"write"}),
        ),
        Operation(
            "member-start-all",
            "adam请求启动所有声明boot规则的成员。",
            _object(_REASON, []),
            {"type": "array", "items": _DECISION},
            start_all,
            effects=frozenset({"write"}),
        ),
        Operation(
            "finish-turn",
            "结束当前局部会话；不改变成员调度状态。",
            _object({}),
            _object({"finished": {"type": "boolean", "enum": [True]}}),
            finish,
        ),
    )


def lifecycle_operation_definitions() -> tuple[Operation, ...]:
    return make_lifecycle_operations(None)
