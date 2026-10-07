"""Admin LLM endpoints: GET /admin/llm/models — the litellm model registry
exposed to the WebUI model dropdown (ADR 0016 §4)."""

from typing import Any

import litellm
from aiohttp import web

from .common import RestHandlerBase

_MODELS_PER_MILLION = 1_000_000


def _chat_models() -> list[dict[str, Any]]:
    """Chat models from the litellm ``model_cost`` registry.

    Only ``provider/model`` keys (the registry also carries bare-name
    aliases of the same entries) with ``mode == "chat"`` are listed.
    Prices are converted from per-token to per-million-token USD.
    """
    models: list[dict[str, Any]] = []
    for key, info in litellm.model_cost.items():
        if "/" not in key or info.get("mode") != "chat":
            continue
        input_cost = info.get("input_cost_per_token")
        output_cost = info.get("output_cost_per_token")
        cache_read_cost = info.get("cache_read_input_token_cost")
        models.append(
            {
                "model": key,
                "provider": info.get("litellm_provider") or key.split("/", 1)[0],
                "context_window": info.get("max_input_tokens"),
                "input_price": (
                    input_cost * _MODELS_PER_MILLION if input_cost is not None else None
                ),
                "output_price": (
                    output_cost * _MODELS_PER_MILLION if output_cost is not None else None
                ),
                "cache_read_price": (
                    cache_read_cost * _MODELS_PER_MILLION if cache_read_cost is not None else None
                ),
                "supports_prompt_caching": bool(info.get("supports_prompt_caching")),
                "supports_function_calling": bool(info.get("supports_function_calling")),
                "deprecation_date": info.get("deprecation_date"),
            }
        )
    models.sort(key=lambda m: (m["provider"], m["model"]))
    return models


class AdminLlmHandlers(RestHandlerBase):
    """Admin LLM route handlers, mixed into ``RESTAPIHandler``."""

    async def handle_list_llm_models(self, request: web.Request) -> web.Response:
        """GET /admin/llm/models - chat model metadata for the WebUI dropdown.

        Returns:
            200 with {"status", "models", "count"}; each model carries
            model/provider, context window, per-million-token prices,
            prompt-caching / function-calling support flags, and an
            optional deprecation date.
        """
        models = _chat_models()
        return self._json_response({"status": "success", "models": models, "count": len(models)})
