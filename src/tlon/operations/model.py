"""Member-bound model completion, shared by programs and model tools."""

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from tlon.operations.core import CallContext, Operation
from tlon.operations.schema import OperationError, UnknownOutcomeError


class ModelClient(Protocol):
    async def chat(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        purpose: str | None = None,
        session_id: str | None = None,
    ) -> Any: ...


def make_model_operations(
    client: ModelClient | None,
    system_prompt: Callable[[], Awaitable[str]] | None,
) -> tuple[Operation, ...]:
    async def complete(context: CallContext, arguments: dict[str, Any]) -> dict[str, Any]:
        if client is None or system_prompt is None:
            raise OperationError("model-call requires member model services")
        try:
            response = await client.chat(
                system_prompt=await system_prompt(),
                messages=[{"role": "user", "content": arguments["prompt"]}],
                tools=None,
                max_tokens=arguments["max_tokens"],
                purpose="program" if context.origin == "program" else "tool",
                session_id=context.run_id,
            )
            if response.tool_calls:
                raise OperationError("model-call returned unrequested tool calls")
            return {"content": response.content or "", "usage": response.usage or {}}
        except OperationError:
            raise
        except Exception as error:
            raise UnknownOutcomeError(
                str(error),
                {"call_id": context.call_id, "outcome": "unknown", "retry_safe": False},
            ) from error

    return (
        Operation(
            "model-call",
            "使用自己的模型与系统提示词生成文本，费用计入自己；不执行工具调用。",
            {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                    "max_tokens": {"type": "integer", "minimum": 1},
                },
                "required": ["prompt", "max_tokens"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "usage": {
                        "type": "object",
                        "properties": {},
                        "required": [],
                        "additionalProperties": True,
                    },
                },
                "required": ["content", "usage"],
                "additionalProperties": False,
            },
            complete,
            effects=frozenset({"write"}),
        ),
    )


def model_operation_definitions() -> tuple[Operation, ...]:
    return make_model_operations(None, None)
