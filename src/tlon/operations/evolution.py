"""Shared operation contracts for the member evolution channel."""

import asyncio
from collections.abc import Callable
from typing import Any, Protocol

from tlon.base.execution import (
    BudgetExceededError,
    ExecutionDeadlineError,
)
from tlon.evolution.ports import PrototypeError
from tlon.operations.core import AsyncHandler, CallContext, Operation
from tlon.operations.invocation import InvocationOutcome
from tlon.operations.program import ProgramChecker, program_check_operation
from tlon.operations.schema import InputError, OperationError, RejectedError, UnknownOutcomeError


class EvolutionService(Protocol):
    async def execute(self, **kwargs: Any) -> Any: ...


def _object(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties) if required is None else required,
        "additionalProperties": False,
    }


_STRING = {"type": "string"}
_NULLABLE = {"type": ["string", "null"]}
_SUMMARY = _object(
    {
        "name": _STRING,
        "display_name": _STRING,
        "description": _STRING,
        "trusted": {"type": "boolean"},
        "parent": _NULLABLE,
        "generation": {"type": "integer"},
    }
)
_STAGING = _object(
    {
        "name": _STRING,
        "display_name": _STRING,
        "description": _STRING,
        "author": _NULLABLE,
        "parent": _NULLABLE,
        "generation": {"type": "integer"},
    }
)
_LINEAGE = _object(
    {
        "name": _STRING,
        "generation": {"type": "integer"},
        "author": _NULLABLE,
        "proposal_note": _NULLABLE,
        "trusted": {"type": "boolean"},
        "parent": _NULLABLE,
        "diff_from_parent": _NULLABLE,
        "broken": {"type": "boolean"},
    }
)
_PROPOSAL = {
    "name": _STRING,
    "parent": _NULLABLE,
    "generation": {"type": "integer"},
    "trusted": {"type": "boolean"},
    "status": {"type": "string", "enum": ["pending_approval"]},
}
_OUTPUTS = {
    "prototype-write": _object({**_PROPOSAL, "display_name": _STRING}),
    "prototype-list": {"type": "array", "items": _SUMMARY},
    "prototype-staging-list": {"type": "array", "items": _STAGING},
    "prototype-lineage": _object({"lineage": {"type": "array", "items": _LINEAGE}}),
    "prototype-read": _object(
        {
            "name": _STRING,
            "digest": _STRING,
            "metadata": _object(
                {
                    **_STAGING["properties"],
                    "trusted": {"type": "boolean"},
                    "proposal_note": _NULLABLE,
                }
            ),
            "files": {"type": "array", "items": _STRING},
            "path": _NULLABLE,
            "content": _NULLABLE,
            "offset": {"type": "integer"},
            "next_offset": {"type": ["integer", "null"]},
        }
    ),
    "prototype-review-submit": _object(
        {
            **dict.fromkeys(
                ("name", "digest", "reason", "reviewer", "run_id", "call_id", "created_at"),
                _STRING,
            ),
            "verdict": {"type": "string", "enum": ["通过", "不通过", "弃权"]},
        }
    ),
}

_CONTRACTS: dict[str, tuple[str, dict[str, Any]]] = {
    "prototype-write": (
        "先用 file-read 读取 tlon/docs/guides/writing-a-prototype.md，按指南用 program-check 校验完整候选。创作一个新的 behavior prototype（纯行为规约，无人格），写入 staging 区。完整 v2 Behavior+模块包；工作流程使用普通函数组合。新 prototype 默认不可信：需人类批准（ops/WebUI）后才能被 member-spawn 使用。behavior 是 v2 EDN handlers 入口，modules 是完整相对路径到源码映射（只描述程序，无法授予工具）。v2 完整模块包不接受参数模板。可选 parent 记录谱系（从哪个既有原型派生），generation 由服务端推导。",
        {
            "type": "object",
            "properties": {
                "behavior": {
                    "type": "string",
                    "description": "完整 v2 EDN，handlers 指向导出模块函数。",
                },
                "modules": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": True,
                    "description": "完整模块包：相对 .tlon 路径到源码字符串",
                },
                "display_name": {
                    "type": "string",
                    "description": "必填行为标签，描述怎样行动；不赋予职业、职位或社会身份",
                },
                "description": {"type": "string", "description": "一句话描述这个原型的行为定位"},
                "parent": {
                    "type": "string",
                    "description": "父原型系统 ID（本原型从哪个原型派生而来；留空表示新的根原型）。父原型必须已存在（trusted 库或 staging 区皆可），generation 由服务端自动推导为父代 + 1",
                },
                "proposal_note": {
                    "type": "string",
                    "description": "本代变更说明：相对父原型改了什么、为什么",
                },
                "lessons": {
                    "type": "string",
                    "description": "随包教训：成员显式整理并提交的经验，落盘为原型的 LESSONS.md；实例化时拷贝到新成员配置，由普通 Lisp 程序决定是否加入模型消息",
                },
            },
            "required": ["behavior", "modules", "display_name"],
        },
    ),
    "prototype-list": (
        "列出可用的 behavior prototype（行为原型）。每个 prototype 是一套预制的行为规约，可作为创建新成员的行为模板；不携带人格，也不赋予职业或职位。返回系统生成的 ID、行为显示名与描述。",
        {"type": "object", "properties": {}, "required": []},
    ),
    "prototype-staging-list": (
        "列出原型暂存区（staging）里等待人类批准的 prototype 提案（只读）。这些是 agent 写好但尚未获人类信任批准（:trusted false）的原型提案，需要人类决策：批准后才进入可信原型库、可被 spawn。返回名称、显示名、描述与作者（author）。",
        {"type": "object", "properties": {}, "required": []},
    ),
    "prototype-lineage": (
        "查询一个 prototype 的谱系：沿 :parent 链向上走到根原型，返回每一代的名称、代数（generation）、作者、变更说明（proposal_note）、trusted 标志，以及相邻两代 behavior.tlon 的 unified diff。父原型已被删除/改名时链在断点处截断并标注 broken。",
        {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "prototype 名称（trusted 库或 staging 区皆可）",
                }
            },
            "required": ["name"],
        },
    ),
    "prototype-read": (
        "显式读取原型材料；先返回元数据、文件清单与材料 digest，再按 path 分页读取。后续读取可携带 digest 校验版本；不调用模型，不自动注入谱系或全部文件。",
        _object(
            {
                "name": _STRING,
                "path": {**_STRING, "description": "清单中的相对文件路径；省略则只返回清单"},
                "offset": {"type": "integer", "minimum": 0, "description": "字符偏移，默认 0"},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 12000,
                    "description": "每页字符数，默认 12000；按 next_offset 继续读取",
                },
                "digest": {
                    **_STRING,
                    "description": "首次读取返回的材料版本；传入后拒绝混读新版本",
                },
            },
            ["name"],
        ),
    ),
    "prototype-review-submit": (
        "提交自己对 staging 原型的咨询性评审；必须携带实际读取材料的 digest、结论和理由。宿主校验版本并绑定成员、run 和调用身份；不调用模型，不自动批准原型。",
        _object(
            {
                "name": _STRING,
                "digest": _STRING,
                "verdict": {"type": "string", "enum": ["通过", "不通过", "弃权"]},
                "reason": {"type": "string", "minLength": 1},
            },
            ["name", "digest", "verdict", "reason"],
        ),
    ),
}


def make_evolution_operations(
    member_id: str,
    *,
    writer: EvolutionService | None = None,
    program_checker: ProgramChecker | None = None,
    catalog: EvolutionService | None = None,
    staging: EvolutionService | None = None,
    lineage: EvolutionService | None = None,
    prototype_reader: Callable[..., dict[str, Any]] | None = None,
    review_submitter: Callable[..., dict[str, Any]] | None = None,
    review_recoverer: Callable[..., dict[str, Any] | None] | None = None,
) -> tuple[Operation, ...]:
    """Bind member identity and authority independently of tool exposure."""
    services = {
        "prototype-write": writer,
        "prototype-list": catalog,
        "prototype-staging-list": staging,
        "prototype-lineage": lineage,
    }
    writes = {"prototype-write", "prototype-review-submit"}

    def handler(name: str) -> AsyncHandler:
        async def execute(context: CallContext, args: dict[str, Any]) -> Any:
            if context.member_id != member_id:
                raise InputError("evolution service belongs to another member")
            try:
                if name == "prototype-read":
                    if prototype_reader is None:
                        raise OperationError("prototype reader is not bound")
                    return await asyncio.to_thread(prototype_reader, **args)
                if name == "prototype-review-submit":
                    if review_submitter is None:
                        raise OperationError("prototype review submitter is not bound")
                    return await asyncio.to_thread(review_submitter, **review_args(context, args))
                service = services[name]
                if service is None:
                    raise OperationError("evolution service is not bound")
                return await service.execute(**args)
            except PrototypeError as error:
                raise RejectedError(
                    str(error), {"call_id": context.call_id, "outcome": "rejected"}
                ) from error
            except (OperationError, BudgetExceededError, ExecutionDeadlineError):
                raise
            except Exception as error:
                if name not in writes:
                    raise
                raise UnknownOutcomeError(
                    str(error),
                    {"outcome": "unknown", "call_id": context.call_id, "retry_safe": False},
                ) from error

        return execute

    def review_args(context: CallContext, args: dict[str, Any]) -> dict[str, Any]:
        return {
            **args,
            "reviewer_id": context.member_id,
            "run_id": context.run_id,
            "call_id": context.call_id,
        }

    async def recover_review(
        context: CallContext, args: dict[str, Any]
    ) -> InvocationOutcome | None:
        if context.member_id != member_id:
            raise InputError("evolution service belongs to another member")
        if review_recoverer is None:
            return None
        try:
            receipt = await asyncio.to_thread(review_recoverer, **review_args(context, args))
        except (PrototypeError, OSError) as error:
            raise UnknownOutcomeError(
                str(error),
                {"call_id": context.call_id, "outcome": "unknown", "retry_safe": False},
            ) from error
        return InvocationOutcome(True, value=receipt) if receipt is not None else None

    operations = [program_check_operation(program_checker)]
    for name, (description, schema) in _CONTRACTS.items():
        schema = {**schema, "additionalProperties": False}
        operations.append(
            Operation(
                name,
                description,
                schema,
                _OUTPUTS[name],
                handler(name),
                effects=frozenset({"write" if name in writes else "read"}),
                recover=recover_review if name == "prototype-review-submit" else None,
            )
        )
    return tuple(operations)


def evolution_operation_definitions() -> tuple[Operation, ...]:
    """Inspect contracts without constructing services or accessing member files."""
    return make_evolution_operations("")
