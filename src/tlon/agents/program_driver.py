"""Replay one frozen member program, returning committed effect values to DSL."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

from tlon.agents.member_spec import MemberRule
from tlon.agents.program_store import ProgramStore
from tlon.base.execution import (
    BudgetExceededError,
    ExecutionDeadlineError,
    ExecutionScope,
    bind_execution,
    current_execution,
)
from tlon.operations.adapters import DSLAdapter
from tlon.operations.control import CONTROL_NAMES, validate_control_forms
from tlon.operations.core import CallContext
from tlon.operations.schema import json_value
from tlosure.builtins import ActionRequest, Builtin, BuiltinCall, EvalError
from tlosure.edn import Form, dumps, loads
from tlosure.eval import DEFAULT_FUEL, DEFAULT_MAX_DEPTH, DEFAULT_WHEN_FUEL, Evaluator

ExecuteEffect = Callable[[ActionRequest, str, int, str], Awaitable[Any]]


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
        on_control: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        execution_scope: ExecutionScope | None = None,
    ) -> None:
        self.on_control = on_control
        self._control_allowed = False
        self.operations = operations
        self.member_id = member_id
        self.on_operation = on_operation
        self.store = store
        self.run_id = run_id
        self.builtins = dict(builtins)
        self.execute = execute
        self.index = 0
        self.execution_scope = execution_scope or ExecutionScope(
            store, run_id, token_budget, "output", parent=current_execution()
        )

    async def run(self, rule: MemberRule, env: dict[str, Any]) -> None:
        with bind_execution(self.execution_scope):
            try:
                await self._run(rule, env)
            except (BudgetExceededError, ExecutionDeadlineError) as error:
                raise EvalError(str(error)) from error
            except asyncio.CancelledError:
                self.execution_scope.cancel()
                raise

    async def _run(self, rule: MemberRule, env: dict[str, Any]) -> None:
        await asyncio.to_thread(self.store.ensure_root, self.run_id)
        await self.execution_scope.initialize()
        validate_control_forms(rule.actions)
        self._control_allowed = False
        shared = (
            Evaluator(self.builtins, rule.fuel, DEFAULT_MAX_DEPTH)
            if rule.fuel is not None
            else None
        )
        if rule.when is not None:
            validate_control_forms([rule.when], allow_direct=False)
            gate = shared or Evaluator(self.builtins, DEFAULT_WHEN_FUEL, DEFAULT_MAX_DEPTH)
            if not await gate.eval_async(rule.when, env, 0, self._call):
                return
        self._control_allowed = bool(
            rule.actions
            and isinstance(rule.actions[0], Form)
            and rule.actions[0]
            and str(rule.actions[0][0]) in CONTROL_NAMES
        )
        for action in rule.actions:
            evaluator = shared or Evaluator(self.builtins, DEFAULT_FUEL, DEFAULT_MAX_DEPTH)
            await evaluator.eval_async(action, env, 0, self._call)

    async def _call(self, call: BuiltinCall) -> Any:
        if call.name in CONTROL_NAMES and not self._control_allowed:
            raise EvalError("nested control operation is forbidden")
        index = self.index
        self.index += 1
        args_edn = dumps(call.args)
        if self.operations is not None and self.operations.owns(call):
            inputs = self.operations._arguments(call.args)
            self.operations.prepare(call)
            context = CallContext(
                self.member_id,
                self.run_id,
                f"program:{self.run_id}:{index}:{call.name}",
                call_path=(str(index),),
                origin="program",
                arguments_json=json.dumps(
                    inputs, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                ),
            )
            found, result = await self.operations.replay(call, context)
            if not found:
                if self.on_operation is not None:
                    self.on_operation()
                result = await self.operations.handle(call, context)
            if call.name in CONTROL_NAMES and self.on_control is not None:
                await self.on_control(json_value(result, edn_keys=True)["scheduling_request"])
            return result
        found, result_edn = await asyncio.to_thread(
            self.store.get_effect, self.run_id, index, call.name, args_edn
        )
        if found:
            assert result_edn is not None
            return loads(result_edn)
        self.execution_scope.check()
        try:
            request = call.invoke()
        except (TypeError, ValueError) as error:
            raise EvalError(f"{call.name} invalid arguments: {error}") from error
        # Closure-backed words run through the kernel. Host query functions
        # return ordinary values; action constructors return request data.
        if isinstance(request, ActionRequest):
            with bind_execution(self.execution_scope.child(f"call:{index}:{call.name}")):
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
        return result
