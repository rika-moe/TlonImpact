"""Replay one frozen member program, returning committed effect values to DSL."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any

from tlon.agents.member_spec import MemberRule
from tlon.agents.program_store import ProgramStore
from tlon.operations.adapters import DSLAdapter
from tlon.operations.core import CallContext
from tlon.operations.schema import UnknownOutcomeError
from tlosure.builtins import ActionRequest, Builtin, BuiltinCall, EvalError
from tlosure.edn import dumps, loads
from tlosure.eval import DEFAULT_FUEL, DEFAULT_MAX_DEPTH, DEFAULT_WHEN_FUEL, Evaluator

ExecuteEffect = Callable[[ActionRequest, str, int, str], Awaitable[Any]]
_HOST_QUERIES = frozenset({"sleeping?", "running?", "lazy?", "member-roster"})


class ProgramDriver:
    """One budget and effect cursor; a fresh driver replays recorded results."""

    def __init__(
        self,
        store: ProgramStore,
        run_id: str,
        builtins: dict[str, Builtin],
        execute: ExecuteEffect,
        *,
        token_budget: int = 4096,
        operations: DSLAdapter | None = None,
        member_id: str = "",
        on_operation: Callable[[], None] | None = None,
    ) -> None:
        self.operations = operations
        self.member_id = member_id
        self.on_operation = on_operation
        self.store = store
        self.run_id = run_id
        self.builtins = {
            name: replace(builtin, pure=False) if name in _HOST_QUERIES else builtin
            for name, builtin in builtins.items()
        }
        self.execute = execute
        self.index = 0
        self.remaining_tokens = token_budget

    async def run(self, rule: MemberRule, env: dict[str, Any]) -> None:
        shared = (
            Evaluator(self.builtins, rule.fuel, DEFAULT_MAX_DEPTH)
            if rule.fuel is not None
            else None
        )
        if rule.when is not None:
            gate = shared or Evaluator(self.builtins, DEFAULT_WHEN_FUEL, DEFAULT_MAX_DEPTH)
            if not await gate.eval_async(rule.when, env, 0, self._call):
                return
        for action in rule.actions:
            evaluator = shared or Evaluator(self.builtins, DEFAULT_FUEL, DEFAULT_MAX_DEPTH)
            await evaluator.eval_async(action, env, 0, self._call)

    async def _call(self, call: BuiltinCall) -> Any:
        index = self.index
        self.index += 1
        args_edn = dumps(call.args)
        found, result_edn = await asyncio.to_thread(
            self.store.get_effect, self.run_id, index, call.name, args_edn
        )
        if found:
            assert result_edn is not None
            result = loads(result_edn)
            if call.name == "llm-call":
                self._charge(result, call)
            return result
        if self.operations is not None and self.operations.owns(call):
            call_id = f"program:{self.run_id}:{index}:{call.name}"
            if self.operations.prepare(call):
                claimed = await asyncio.to_thread(
                    self.store.claim_intent, self.run_id, index, call.name, args_edn
                )
                if not claimed:
                    raise UnknownOutcomeError(
                        "Previous operation dispatch has no recorded result; automatic retry refused",
                        {"call_id": call_id, "outcome": "unknown", "retry_safe": False},
                    )
            if self.on_operation is not None:
                self.on_operation()
            result = await self.operations.handle(
                call,
                CallContext(
                    self.member_id,
                    self.run_id,
                    call_id,
                    effect_index=index,
                    arguments_edn=args_edn,
                ),
            )
            await asyncio.to_thread(
                self.store.record_effect, self.run_id, index, call.name, args_edn, dumps(result)
            )
            return result
        try:
            request = call.invoke()
        except (TypeError, ValueError) as error:
            raise EvalError(f"{call.name} invalid arguments: {error}") from error
        # Closure-backed words run through the kernel. Host query functions
        # return ordinary values; action constructors return request data.
        if isinstance(request, ActionRequest):
            if request.kind == "llm_call":
                if self.remaining_tokens <= 0:
                    raise EvalError("member program LLM output budget exhausted")
                request = ActionRequest(
                    request.kind,
                    {
                        **request.data,
                        "max_tokens": min(request.data["max_tokens"], self.remaining_tokens),
                    },
                )
            result = await self.execute(request, call.name, index, args_edn)
        elif isinstance(request, list) and all(isinstance(item, ActionRequest) for item in request):
            result = []
            for offset, item in enumerate(request):
                result.append(await self.execute(item, f"{call.name}/{offset}", index, args_edn))
        else:
            result = request
        await asyncio.to_thread(
            self.store.record_effect, self.run_id, index, call.name, args_edn, dumps(result)
        )
        if call.name == "llm-call":
            self._charge(result, call)
        return result

    def _charge(self, result: Any, call: BuiltinCall) -> None:
        # Reserve the requested output allowance. Missing provider usage can
        # never make the next call free; replay applies the same reservation.
        requested = next(
            (value for key, value in call.args[0].items() if str(key) == "max-tokens"),
            self.remaining_tokens,
        )
        self.remaining_tokens -= min(requested, self.remaining_tokens)
