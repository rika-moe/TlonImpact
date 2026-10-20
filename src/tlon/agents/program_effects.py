"""Generic I/O adapters for member programs; business policy belongs in DSL."""

from collections.abc import Awaitable, Callable
from typing import Any

from tlon.agents.program_store import ProgramStore
from tlon.base.llm_client import LLMClient
from tlosure.builtins import ActionRequest, EvalError
from tlosure.edn import Keyword


def _keywords(value: Any) -> Any:
    if isinstance(value, dict):
        return {Keyword(str(key).replace("_", "-")): _keywords(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_keywords(item) for item in value]
    return value


class ProgramEffects:
    """Use member-owned clients, identity and quotas for every program effect."""

    def __init__(
        self,
        *,
        llm_client: LLMClient | None = None,
        system_prompt: Callable[[], Awaitable[str]] | None = None,
    ) -> None:
        self._llm_client = llm_client
        self._system_prompt = system_prompt

    async def execute(
        self,
        request: ActionRequest,
        request_id: str,
        run_id: str,
        index: int,
        store: ProgramStore,
        args_edn: str,
    ) -> Any:
        data = request.data
        if request.kind == "llm_call":
            if self._llm_client is None or self._system_prompt is None:
                raise EvalError("llm-call requires member model services")
            response = await self._llm_client.chat(
                system_prompt=await self._system_prompt(),
                messages=[{"role": "user", "content": data["prompt"]}],
                tools=None,
                max_tokens=data["max_tokens"],
                purpose="program",
                session_id=run_id,
            )
            if response.tool_calls:
                raise EvalError("llm-call returned unrequested tool calls")
            return _keywords({"content": response.content or "", "usage": response.usage or {}})
        raise ValueError(f"Unknown program effect: {request.kind}")
