"""One member-bound model boundary for ordinary programs."""

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus
from tlon.base.execution import ExecutionScope, current_execution
from tlon.base.llm_client import LLMResponse
from tlon.base.logger import get_logger
from tlon.operations.invocation import InvocationOutcome
from tlon.operations.schema import InputError, RecoveryMismatchError

logger = get_logger(__name__)


class ModelClient(Protocol):
    async def chat(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        purpose: str | None = None,
        run_id: str | None = None,
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
        *,
        event_bus: EventBus | None = None,
        member_id: str = "unknown",
        model_name: str = "unknown",
    ) -> None:
        self.client = client
        self.system_prompt = system_prompt
        self.tool_definitions = tool_definitions
        self.event_bus = event_bus
        self.member_id = member_id
        self.model_name = model_name

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
        run_id: str | None = None,
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
        if history is None:
            await self._publish(
                EventType.AGENT_LLM_REQUEST,
                {"messages_count": len(messages), "tools_count": len(selected)},
            )
        started = time.monotonic()
        response = await self.client.chat(
            system_prompt=host["system_prompt"],
            messages=messages,
            tools=selected or None,
            max_tokens=max_tokens,
            purpose=purpose,
            run_id=run_id,
            cache_prefix_len=cache_prefix_len,
        )
        if history is None:
            usage = response.usage or {}
            raw_details = usage.get("prompt_tokens_details")
            details: dict[str, Any] = raw_details if isinstance(raw_details, dict) else {}
            await self._publish(
                EventType.AGENT_LLM_RESPONSE,
                {
                    "duration_ms": (time.monotonic() - started) * 1000,
                    "tool_call_count": len(response.tool_calls or []),
                    "finish_reason": response.finish_reason,
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "completion_tokens": usage.get("completion_tokens", 0),
                    "cached_tokens": details.get("cached_tokens", 0),
                    "cache_write_tokens": details.get("cache_write_tokens", 0),
                },
            )
        return response

    async def _publish(self, kind: EventType, payload: dict[str, Any]) -> None:
        if self.event_bus is None:
            return
        try:
            await self.event_bus.publish(
                Event(
                    type=kind,
                    payload={"agent_id": self.member_id, "model": self.model_name, **payload},
                    source=self.member_id,
                )
            )
        except Exception as error:
            logger.warning("model_observation_failed", error=str(error))

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
            "max_tokens": arguments.get("max_tokens"),
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
