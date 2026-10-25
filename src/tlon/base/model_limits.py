"""Resolve model capabilities and size complete requests consistently."""

import json
from typing import Any, Literal

import litellm

from tlon.base.config import DEFAULT_CONTEXT_WINDOW, LLMConfig
from tlon.base.execution import BudgetExceededError


def resolve_model_limits(config: LLMConfig) -> LLMConfig:
    """Keep explicit/frozen capabilities; unresolved output capability is an error."""
    if config.max_output_tokens is not None and config.context_window is not None:
        return config
    metadata: dict[str, Any] = {}
    try:
        metadata = dict(litellm.get_model_info(config.model_name or ""))
    except Exception as error:
        if config.max_output_tokens is None:
            raise ValueError(
                f"Cannot resolve model output capability: {config.model_name}"
            ) from error
    output = config.max_output_tokens
    if output is None:
        output = metadata.get("max_output_tokens")
        if type(output) is not int or output <= 0:
            raise ValueError(f"Model max_output_tokens is unresolved: {config.model_name}")
    window = config.context_window
    if window is None:
        window = metadata.get("max_input_tokens")
        if type(window) is not int or window <= 0:
            window = DEFAULT_CONTEXT_WINDOW
    return config.model_copy(update={"max_output_tokens": output, "context_window": window})


def estimate_model_input(
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
) -> int:
    """Include system/messages and serialized tools (boolean JSON Schema is valid)."""
    tokens = litellm.token_counter(model=model, messages=messages)
    if tools:
        tokens += litellm.token_counter(
            model=model,
            text=json.dumps(tools, ensure_ascii=False, separators=(",", ":")),
        )
    return int(tokens)


def model_output_limit(
    config: LLMConfig,
    input_tokens: int,
    requested: int | None = None,
    *,
    remaining: int | None = None,
    measure: Literal["output", "total"] = "output",
) -> int:
    """Zero means context pressure; exhausted run resources are a budget error."""
    if config.max_output_tokens is None or config.context_window is None:
        raise ValueError("Model output capability and context window must be resolved")
    if requested is not None and (type(requested) is not int or requested <= 0):
        raise ValueError("max_tokens must be a positive integer")
    budget = (
        remaining - (input_tokens if measure == "total" else 0) if remaining is not None else None
    )
    if budget is not None and budget <= 0:
        raise BudgetExceededError("Execution token allowance exhausted")
    limits = [config.max_output_tokens, max(config.context_window - input_tokens, 0)]
    if requested is not None:
        limits.append(requested)
    if budget is not None:
        limits.append(budget)
    return min(limits)
