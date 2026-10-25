"""Read the frozen model window and estimate the complete request without sampling."""

import asyncio
from typing import Any

from tlon.base.config import LLMConfig
from tlon.base.execution import current_execution
from tlon.base.model_limits import estimate_model_input, model_output_limit
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
            estimate_model_input,
            config["model_name"],
            [{"role": "system", "content": host["system_prompt"]}, *arguments["messages"]],
            selected,
        )
        await scope.initialize()
        budget = await asyncio.to_thread(scope.store.budget_usage, scope.root_id)
        output = model_output_limit(
            LLMConfig.model_validate(config),
            tokens,
            arguments.get("max_tokens"),
            remaining=budget["remaining"],
            measure=scope.measure,
        )
        return {
            "input_tokens": tokens,
            "context_window": window,
            "output_tokens": output,
            "fits": output > 0,
            "token_budget_remaining": budget["remaining"],
            "token_budget_limit": budget["token_limit"],
            "token_budget_measure": budget["measure"],
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
                "output_tokens": {"type": "integer", "minimum": 0},
                "fits": {"type": "boolean"},
                "token_budget_remaining": {"type": "integer", "minimum": 0},
                "token_budget_limit": {"type": "integer", "minimum": 0},
                "token_budget_measure": {"type": "string", "enum": ["output", "total"]},
            },
            "required": [
                "input_tokens",
                "context_window",
                "output_tokens",
                "fits",
                "token_budget_remaining",
                "token_budget_limit",
                "token_budget_measure",
            ],
            "additionalProperties": False,
        },
        inspect,
        effects=frozenset({"read"}),
    )
