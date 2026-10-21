"""One member-bound model boundary for programs and legacy stages."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from tlon.base.execution import ExecutionScope, current_execution
from tlon.base.llm_client import LLMResponse
from tlon.operations.invocation import InvocationOutcome
from tlon.operations.schema import InputError, RecoveryMismatchError


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
        cache_prefix_len: int | None = None,
    ) -> LLMResponse: ...


def response_data(response: LLMResponse) -> dict[str, Any]:
    """Public program representation; never erase a tool-only response's null content."""
    return {
        "content": response.content,
        "tool_calls": [
            {"id": call.id, "name": call.function_name, "arguments": call.arguments}
            for call in response.tool_calls or []
        ],
        "finish_reason": response.finish_reason,
        "usage": response.usage or {},
        "reasoning_content": response.reasoning_content,
    }


class ModelService:
    def __init__(
        self,
        client: ModelClient,
        system_prompt: Callable[[], Awaitable[str]],
        tool_definitions: Callable[[], list[dict[str, Any]]] = lambda: [],
    ) -> None:
        self.client = client
        self.system_prompt = system_prompt
        self.tool_definitions = tool_definitions

    async def host_context(self) -> dict[str, Any]:
        scope = current_execution()
        if scope is not None:
            frozen = await asyncio.to_thread(scope.store.load_model_context, scope.root_id, "host")
            if frozen is not None:
                return frozen
        value = {"system_prompt": await self.system_prompt(), "tools": self.tool_definitions()}
        if scope is not None:
            return await asyncio.to_thread(
                scope.store.freeze_model_context, scope.root_id, "host", value
            )
        return value

    @staticmethod
    def select_tools(catalog: list[dict[str, Any]], names: list[str]) -> list[dict[str, Any]]:
        by_name = {definition["function"]["name"]: definition for definition in catalog}
        if len(set(names)) != len(names):
            raise InputError("model-call tools contains duplicate names")
        unknown = set(names) - by_name.keys()
        if unknown:
            raise InputError(f"Unknown model tools: {sorted(unknown)}")
        return [by_name[name] for name in names]

    async def request(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[str] | None = None,
        max_tokens: int | None = None,
        purpose: str | None = None,
        session_id: str | None = None,
        cache_prefix_len: int | None = None,
    ) -> LLMResponse:
        host = await self.host_context()
        names = tools if tools is not None else [item["function"]["name"] for item in host["tools"]]
        selected = self.select_tools(host["tools"], names)
        # History uses the frozen catalog. Fresh effects must also pass today's authority.
        scope = current_execution()
        history = None
        if scope is not None:
            history = await asyncio.to_thread(
                scope.store.lookup_model_path, scope.root_id, scope.next_model_path()
            )
        if history is None:
            current = self.select_tools(self.tool_definitions(), names)
            if current != selected:
                raise RecoveryMismatchError("Model tool definitions differ from frozen catalog")
        return await self.client.chat(
            system_prompt=host["system_prompt"],
            messages=messages,
            tools=selected or None,
            max_tokens=max_tokens,
            purpose=purpose,
            session_id=session_id,
            cache_prefix_len=cache_prefix_len,
        )

    async def recover(
        self, scope: ExecutionScope, arguments: dict[str, Any], call_id: str
    ) -> InvocationOutcome | None:
        """Read only: bridge a committed model result to a missing outer receipt."""
        state = await asyncio.to_thread(
            scope.store.lookup_model_path, scope.root_id, (*scope.path, "model:0")
        )
        if state is None or state["status"] == "unknown":
            return None
        host = await asyncio.to_thread(scope.store.load_model_context, scope.root_id, "host")
        if host is None:
            raise RecoveryMismatchError("Committed model result has no frozen host context")
        selected = self.select_tools(host["tools"], arguments.get("tools", []))
        request = json.loads(state["request_json"])["request"]
        expected = {
            "messages": [{"role": "system", "content": host["system_prompt"]}]
            + arguments["messages"],
            "tools": selected or None,
            "max_tokens": arguments["max_tokens"],
            "cache_prefix_len": arguments.get("cache_prefix_len"),
        }
        if any(request.get(key) != value for key, value in expected.items()):
            raise RecoveryMismatchError("Committed model request differs from program arguments")
        if state["status"] == "rejected":
            return InvocationOutcome(
                False,
                error_type="RejectedError",
                error_message=state["message"],
                error_data={"call_id": call_id, "outcome": "rejected"},
            )
        return InvocationOutcome(
            True, value=response_data(LLMResponse.from_dict(state["response"]))
        )
