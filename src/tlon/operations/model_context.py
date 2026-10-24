"""Read the frozen model window and estimate the complete request without sampling."""

import asyncio
import json
from typing import Any

import litellm

from tlon.base.execution import current_execution
from tlon.operations.core import CallContext, Operation
from tlon.operations.model_service import ModelService
from tlon.operations.schema import OperationError, RecoveryMismatchError


def context_operation(service: ModelService | None, input_schema: dict[str, Any]) -> Operation:
    async def inspect(context: CallContext, arguments: dict[str, Any]) -> dict[str, Any]:
        scope = current_execution()
        if service is None or scope is None:
            raise OperationError(
                "model-context requires member model services and an execution scope"
            )
        config = await asyncio.to_thread(scope.store.load_model_context, scope.root_id, "config")
        if config is None:
            raise RecoveryMismatchError("model-context requires frozen model configuration")
        window = config.get("context_window")
        if type(window) is not int or window < 1:
            raise OperationError("Member model context window is unresolved")
        host = await service.host_context()
        selected = service.select_tools(host["tools"], arguments.get("tools", []))
        tokens = await asyncio.to_thread(
            litellm.token_counter,
            model=config["model_name"],
            messages=[{"role": "system", "content": host["system_prompt"]}, *arguments["messages"]],
        )
        # LiteLLM's tool-schema walker does not accept JSON Schema boolean nodes.
        # Count serialized tools just as LLMClient's total-budget estimator does.
        if selected:
            tokens += await asyncio.to_thread(
                litellm.token_counter,
                model=config["model_name"],
                text=json.dumps(selected, ensure_ascii=False, separators=(",", ":")),
            )
        output = min(config["max_tokens"], arguments.get("max_tokens", config["max_tokens"]))
        return {
            "input_tokens": tokens,
            "context_window": window,
            "output_tokens": output,
            "fits": tokens + output <= window,
        }

    return Operation(
        "model-context",
        "Estimate the complete frozen model request size and window; does not call the provider.",
        input_schema,
        {
            "type": "object",
            "properties": {
                "input_tokens": {"type": "integer", "minimum": 0},
                "context_window": {"type": "integer", "minimum": 1},
                "output_tokens": {"type": "integer", "minimum": 1},
                "fits": {"type": "boolean"},
            },
            "required": ["input_tokens", "context_window", "output_tokens", "fits"],
            "additionalProperties": False,
        },
        inspect,
        effects=frozenset({"read"}),
    )
