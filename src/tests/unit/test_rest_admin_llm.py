"""Admin LLM registry endpoint (ADR 0016 §4): GET /admin/llm/models.

The handler is called directly with a stub request — the data source is
the real litellm ``model_cost`` registry, no mocking.
"""

import json

import litellm

from tlon.app.server.api.rest import RESTAPIHandler


class StubRequest:
    """Minimal stand-in for aiohttp.web.Request."""

    def __init__(self) -> None:
        self.match_info = {}


def _handler() -> RESTAPIHandler:
    # The LLM registry endpoint touches no runtime state — no facades.
    return RESTAPIHandler(None)


class TestListLlmModels:
    async def test_response_shape(self):
        handler = _handler()

        resp = await handler.handle_list_llm_models(StubRequest())

        assert resp.status == 200
        body = json.loads(resp.text)
        assert body["status"] == "success"
        models = body["models"]
        assert body["count"] == len(models)
        assert len(models) > 1000  # the registry carries thousands of chat models

    async def test_only_provider_slash_chat_models(self):
        handler = _handler()
        resp = await handler.handle_list_llm_models(StubRequest())
        models = json.loads(resp.text)["models"]

        for entry in models:
            assert "/" in entry["model"]
            # every listed model exists in the registry as a chat model
            assert litellm.model_cost[entry["model"]]["mode"] == "chat"

    async def test_fields_present_and_priced_per_million(self):
        handler = _handler()
        resp = await handler.handle_list_llm_models(StubRequest())
        models = json.loads(resp.text)["models"]

        expected_keys = {
            "model",
            "provider",
            "context_window",
            "input_price",
            "output_price",
            "cache_read_price",
            "supports_prompt_caching",
            "supports_function_calling",
            "deprecation_date",
        }
        for entry in models:
            assert set(entry.keys()) == expected_keys

        # Spot-check against the raw registry (per-token * 1e6 conversion).
        info = litellm.model_cost["deepseek/deepseek-chat"]
        entry = next(m for m in models if m["model"] == "deepseek/deepseek-chat")
        assert entry["provider"] == "deepseek"
        assert entry["context_window"] == info["max_input_tokens"]
        assert entry["input_price"] == info["input_cost_per_token"] * 1_000_000
        assert entry["output_price"] == info["output_cost_per_token"] * 1_000_000
        assert entry["cache_read_price"] == info["cache_read_input_token_cost"] * 1_000_000
        assert entry["supports_prompt_caching"] is True
        assert entry["supports_function_calling"] is True
        # A model without a cache-read price reports null, not a crash.
        no_cache = next(m for m in models if m["cache_read_price"] is None)
        assert no_cache["model"]

    async def test_sorted_by_provider_then_model(self):
        handler = _handler()
        resp = await handler.handle_list_llm_models(StubRequest())
        models = json.loads(resp.text)["models"]

        keys = [(m["provider"], m["model"]) for m in models]
        assert keys == sorted(keys)
