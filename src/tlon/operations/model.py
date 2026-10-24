"""Member-bound model completion, shared by programs and model tools."""

from collections.abc import Awaitable, Callable
from typing import Any

from tlon.base.events.event_bus import EventBus
from tlon.base.execution import (
    BudgetExceededError,
    ExecutionDeadlineError,
    ModelRequestRejectedError,
    UnknownModelOutcomeError,
)
from tlon.operations.core import CallContext, Operation
from tlon.operations.invocation import InvocationOutcome
from tlon.operations.model_context import context_operation
from tlon.operations.model_service import ModelClient, ModelService, response_data
from tlon.operations.schema import OperationError, RejectedError, UnknownOutcomeError

MODEL_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "messages": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "role": {"type": "string", "enum": ["user", "assistant", "tool"]},
                    "content": {"type": ["string", "null"]},
                    "tool_calls": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "type": {"type": "string", "enum": ["function"]},
                                "function": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "arguments": {"type": "string"},
                                    },
                                    "required": ["name", "arguments"],
                                    "additionalProperties": False,
                                },
                            },
                            "required": ["id", "type", "function"],
                            "additionalProperties": False,
                        },
                    },
                    "tool_call_id": {"type": "string"},
                    "reasoning_content": {"type": ["string", "null"]},
                },
                "required": ["role", "content"],
                "additionalProperties": False,
            },
        },
        "tools": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
        "max_tokens": {"type": "integer", "minimum": 1},
        "cache_prefix_len": {"type": "integer", "minimum": 0},
    },
    "required": ["messages"],
    "additionalProperties": False,
}


def make_model_operations(
    client: ModelClient | None,
    system_prompt: Callable[[], Awaitable[str]] | None,
    tool_definitions: Callable[[], list[dict[str, Any]]] = lambda: [],
    *,
    event_bus: EventBus | None = None,
    member_id: str = "unknown",
    model_name: str = "unknown",
) -> tuple[Operation, ...]:
    service = (
        ModelService(
            client,
            system_prompt,
            tool_definitions,
            event_bus=event_bus,
            member_id=member_id,
            model_name=model_name,
        )
        if client and system_prompt
        else None
    )

    async def recover(context: CallContext, arguments: dict[str, Any]) -> InvocationOutcome | None:
        if service is None or context.execution is None:
            return None
        return await service.recover(context.execution, arguments, context.call_id)

    async def complete(context: CallContext, arguments: dict[str, Any]) -> dict[str, Any]:
        if service is None:
            raise OperationError("model-call requires member model services")
        try:
            response = await service.request(
                messages=arguments["messages"],
                tools=arguments.get("tools", []),
                max_tokens=arguments.get("max_tokens"),
                cache_prefix_len=arguments.get("cache_prefix_len"),
                purpose="program" if context.origin == "program" else "tool",
                session_id=context.run_id,
            )
            return response_data(response)
        except (OperationError, BudgetExceededError, ExecutionDeadlineError):
            raise
        except ModelRequestRejectedError as error:
            raise RejectedError(
                str(error), {"call_id": context.call_id, "outcome": "rejected"}
            ) from error
        except UnknownModelOutcomeError as error:
            raise UnknownOutcomeError(
                str(error),
                {
                    "call_id": context.call_id,
                    "outcome": "unknown",
                    "retry_safe": False,
                    "unknown_source": "model",
                },
            ) from error
        except Exception as error:
            raise UnknownOutcomeError(
                str(error),
                {
                    "call_id": context.call_id,
                    "outcome": "unknown",
                    "retry_safe": False,
                    "unknown_source": "model",
                },
            ) from error

    return (
        Operation(
            "model-call",
            "使用自己的模型与系统提示词生成结构化响应；不执行工具调用。",
            MODEL_INPUT_SCHEMA,
            {
                "type": "object",
                "properties": {
                    "content": {"type": ["string", "null"]},
                    "tool_calls": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "name": {"type": "string"},
                                "arguments": {
                                    "type": "object",
                                    "properties": {},
                                    "required": [],
                                    "additionalProperties": True,
                                },
                            },
                            "required": ["id", "name", "arguments"],
                            "additionalProperties": False,
                        },
                    },
                    "finish_reason": {"type": "string"},
                    "usage": {
                        "type": "object",
                        "properties": {},
                        "required": [],
                        "additionalProperties": True,
                    },
                    "reasoning_content": {"type": ["string", "null"]},
                },
                "required": [
                    "content",
                    "tool_calls",
                    "finish_reason",
                    "usage",
                    "reasoning_content",
                ],
                "additionalProperties": False,
            },
            complete,
            effects=frozenset({"write"}),
            recover=recover,
        ),
        context_operation(service, MODEL_INPUT_SCHEMA),
    )


def model_operation_definitions() -> tuple[Operation, ...]:
    return make_model_operations(None, None)
