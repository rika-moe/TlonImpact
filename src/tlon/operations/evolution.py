"""Shared operation contracts for the member evolution channel."""

from typing import Any, Protocol

from tlon.base.execution import (
    BudgetExceededError,
    ExecutionDeadlineError,
    ModelRequestRejectedError,
)
from tlon.operations.core import AsyncHandler, CallContext, Operation
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
    "behavior-propose": _object(_PROPOSAL),
    "prototype-list": {"type": "array", "items": _SUMMARY},
    "prototype-staging-list": {"type": "array", "items": _STAGING},
    "prototype-lineage": _object({"lineage": {"type": "array", "items": _LINEAGE}}),
    "prototype-review": _object(
        {
            "name": _STRING,
            "verdict": {"type": "string", "enum": ["通过", "不通过", "弃权"]},
            "reviewer": _STRING,
            "model": _STRING,
        }
    ),
}

_CONTRACTS: dict[str, tuple[str, dict[str, Any]]] = {
    "prototype-write": (
        "创作一个新的 behavior prototype（纯行为规约，无人格），写入 staging 区。规则统一使用 on、when、actions；会话通过 run-loop 动作调用。新 prototype 默认不可信：需人类批准（ops/WebUI）后才能被 member-spawn 使用。behavior 是 v1 schema 的纯数据 EDN（只描述行为，无法授予工具）。可声明 :parameters 以支持 spawn 时参数化。可选 parent 记录谱系（从哪个既有原型派生），generation 由服务端推导。",
        {
            "type": "object",
            "properties": {
                "behavior": {
                    "type": "string",
                    "description": "行为规约（EDN 字符串）。统一 on/when/actions；会话使用 run-loop 动作。可选 :parameters，引用变量用 ${name}。",
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
                    "description": "随包教训：退休成员蒸馏出的经验，落盘为原型的 LESSONS.md；实例化时拷贝给新成员，由 spec 的 (lessons) context section 注入对话",
                },
            },
            "required": ["behavior", "display_name"],
        },
    ),
    "behavior-propose": (
        "基于自己的个体经验提出下一代行为原型（世代通道的经验入口）。只需给出变更意图与行为显示名：工具内部读你当前的 behavior.tlon 与记忆（claims 与蒸馏教训），由一次独立的起草调用产出完整的新 spec，并以你自己的 prototype 为父原型写入 staging 区（proposal_note 记意图、lessons 随包）。与 prototype-write 的区别：那是裸写通道（自带全文），这里由经验驱动起草。提案不可信：需人类批准（ops/WebUI）后才能 member-spawn 派生新世代。",
        {
            "type": "object",
            "properties": {
                "intent": {
                    "type": "string",
                    "description": "变更意图：想改什么、为什么（自然语言，记为 proposal_note）",
                },
                "display_name": {
                    "type": "string",
                    "description": "必填行为标签，描述怎样行动；不赋予职业、职位或社会身份",
                },
                "description": {"type": "string", "description": "一句话描述这个原型的行为定位"},
            },
            "required": ["intent", "display_name"],
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
    "prototype-review": (
        "评审一个 staging 区的原型提案（异质评审）。只点名提案——结论不由你当场给：工具内部用你自己的模型做一次专项评审调用，读取提案的 behavior 全文、随包教训与谱系链（含每代 diff）后产出 verdict（通过/不通过/弃权）与理由，追加记入提案目录的 REVIEW.md（含时间、你的 id 与你的模型）。评审是咨询性的：不自动晋级，人类审批时参考这些记录做决定。提案不存在或不在 staging 时失败。",
        {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "staging 区提案名称（trusted 库原型不可评审）",
                }
            },
            "required": ["name"],
        },
    ),
}


def make_evolution_operations(
    member_id: str,
    is_full: bool,
    *,
    writer: EvolutionService | None = None,
    behavior_proposer: EvolutionService | None = None,
    catalog: EvolutionService | None = None,
    staging: EvolutionService | None = None,
    lineage: EvolutionService | None = None,
    reviewer: EvolutionService | None = None,
) -> tuple[Operation, ...]:
    """Bind member identity and authority independently of tool exposure."""
    services = {
        "prototype-write": writer,
        "behavior-propose": behavior_proposer,
        "prototype-list": catalog,
        "prototype-staging-list": staging,
        "prototype-lineage": lineage,
        "prototype-review": reviewer,
    }
    writes = {"prototype-write", "behavior-propose", "prototype-review"}

    def handler(name: str) -> AsyncHandler:
        async def execute(context: CallContext, args: dict[str, Any]) -> Any:
            if context.member_id != member_id:
                raise InputError("evolution service belongs to another member")
            if not is_full and name in {
                "prototype-write",
                "behavior-propose",
            }:
                raise RejectedError("evolution operations require a full member", {})
            service = services[name]
            if service is None:
                raise OperationError("evolution service is not bound")
            try:
                return await service.execute(**args)
            except (OperationError, BudgetExceededError, ExecutionDeadlineError):
                raise
            except ModelRequestRejectedError as error:
                raise RejectedError(
                    str(error), {"call_id": context.call_id, "outcome": "rejected"}
                ) from error
            except Exception as error:
                if name not in writes:
                    raise
                raise UnknownOutcomeError(
                    str(error),
                    {"outcome": "unknown", "call_id": context.call_id, "retry_safe": False},
                ) from error

        return execute

    operations = []
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
            )
        )
    return tuple(operations)


def evolution_operation_definitions() -> tuple[Operation, ...]:
    """Inspect contracts without constructing services or accessing member files."""
    return make_evolution_operations("", False)
