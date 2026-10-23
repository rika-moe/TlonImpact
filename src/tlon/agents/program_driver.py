"""Replay one frozen member program, returning committed effect values to DSL."""

import asyncio
import json
from collections.abc import Callable
from typing import Any

from tlon.agents.program_store import ProgramStore
from tlon.base.execution import (
    BudgetExceededError,
    ExecutionDeadlineError,
    ExecutionScope,
    bind_execution,
    current_execution,
)
from tlon.operations.core import CallContext
from tlon.operations.dsl import DSLAdapter
from tlosure.builtins import Builtin, BuiltinCall, Closure, EvalError
from tlosure.edn import Form, Symbol
from tlosure.eval import DEFAULT_MAX_DEPTH, Evaluator


class ProgramDriver:
    """One budget and effect cursor; a fresh driver replays recorded results."""

    def __init__(
        self,
        store: ProgramStore,
        run_id: str,
        builtins: dict[str, Builtin],
        *,
        token_budget: int = 4096,
        operations: DSLAdapter,
        member_id: str = "",
        on_operation: Callable[[], None] | None = None,
        execution_scope: ExecutionScope | None = None,
    ) -> None:
        self.operations = operations
        self.member_id = member_id
        self.on_operation = on_operation
        self.store = store
        self.run_id = run_id
        self.builtins = dict(builtins)
        self.index = 0
        self.execution_scope = execution_scope or ExecutionScope(
            store, run_id, token_budget, "output", parent=current_execution()
        )

    async def run_entry(self, entry: Closure, event: dict[str, Any], *, fuel: int) -> Any:
        """Run one module entry with the same effect cursor and root budget."""
        with bind_execution(self.execution_scope):
            await asyncio.to_thread(self.store.ensure_root, self.run_id)
            await self.execution_scope.initialize()
            evaluator = Evaluator(self.builtins, fuel, DEFAULT_MAX_DEPTH)
            form = Form([Symbol("__entry"), Form([Symbol("quote"), event])])
            try:
                return await evaluator.eval_async(form, {"__entry": entry}, 0, self._call)
            except (BudgetExceededError, ExecutionDeadlineError) as error:
                raise EvalError(str(error)) from error
            except asyncio.CancelledError:
                self.execution_scope.cancel()
                raise

    async def _call(self, call: BuiltinCall) -> Any:
        index = self.index
        self.index += 1
        if self.operations.owns(call):
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
            if self.operations.is_dispatch(call):
                if self.on_operation is not None:
                    self.on_operation()
                return await self.operations.dispatch(call, context)
            found, result = await self.operations.replay(call, context)
            if not found:
                if self.on_operation is not None:
                    self.on_operation()
                result = await self.operations.handle(call, context)
            return result
        raise EvalError(f"effectful builtin is not an operation: {call.name}")
