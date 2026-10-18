"""Generic I/O adapters for member programs; business policy belongs in DSL."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from tlon.agents.program_store import ProgramStore
from tlon.base.llm_client import LLMClient
from tlon.organization.mail.post_office import PostOffice, PostRejected
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
        from_address: str,
        llm_client: LLMClient | None = None,
        system_prompt: Callable[[], Awaitable[str]] | None = None,
        post_office: PostOffice | None = None,
        mentor_cc: str | None = None,
    ) -> None:
        self._from_address = from_address
        self._llm_client = llm_client
        self._system_prompt = system_prompt
        self._post_office = post_office
        self._mentor_cc = mentor_cc

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
        if request.kind == "state_get":
            return await asyncio.to_thread(store.state_get, data["key"])
        if request.kind == "state_put":
            return await asyncio.to_thread(
                store.atomic_state_put_record,
                run_id,
                index,
                "state-put",
                args_edn,
                data["key"],
                data["value"],
            )
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
        if request.kind == "send_mail":
            if self._post_office is None:
                raise EvalError("send-mail requires a post office")
            to = data["to"]
            cc = [self._mentor_cc] if self._mentor_cc and self._mentor_cc not in to else []
            try:
                receipt = await self._post_office.send(
                    from_address=self._from_address,
                    to=to,
                    cc=cc,
                    subject=data.get("subject"),
                    content=data["content"],
                    request_id=data.get("request_id") or request_id,
                )
            except PostRejected as error:
                return _keywords(
                    {
                        "status": "rejected",
                        "reason": error.reason,
                        "invalid_recipients": error.invalid_recipients,
                    }
                )
            return _keywords({"status": "accepted", **receipt.to_dict()})
        raise ValueError(f"Unknown program effect: {request.kind}")
