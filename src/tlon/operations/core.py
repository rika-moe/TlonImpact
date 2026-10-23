"""Immutable host operation definitions, member-scoped registry and invocation."""

import asyncio
import hashlib
import inspect
import json
import math
import re
import time
import weakref
from collections.abc import Awaitable, Callable, Iterable
from contextlib import nullcontext, suppress
from dataclasses import dataclass, replace
from typing import Any, ClassVar, Literal, cast

from tlon.base.execution import (
    BudgetExceededError,
    ExecutionDeadlineError,
    ExecutionScope,
    MemberWriteGate,
    bind_execution,
    current_execution,
)
from tlon.operations.invocation import Invocation, InvocationOutcome, InvocationStore
from tlon.operations.schema import (
    DefinitionError,
    InputError,
    OperationError,
    OutputError,
    RecoveryMismatchError,
    RejectedError,
    UnknownOutcomeError,
    admit_schema,
    json_value,
    validate,
)
from tlosure.builtins import ARITHMETIC_BUILTINS, CORE_BUILTINS
from tlosure.eval import SPECIAL_FORMS

Effect = Literal["read", "write"]


@dataclass(frozen=True)
class CallContext:
    """Host-created identity, bound outside business arguments."""

    member_id: str
    run_id: str
    call_id: str
    call_path: tuple[str, ...] = ()
    origin: Literal["program", "tool", "direct"] = "direct"
    arguments_json: str | None = None
    invocation: Invocation | None = None
    execution: ExecutionScope | None = None


Handler = Callable[[CallContext, dict[str, Any]], Any]
AsyncHandler = Callable[[CallContext, dict[str, Any]], Awaitable[Any]]
RecoverHandler = Callable[[CallContext, dict[str, Any]], Awaitable[InvocationOutcome | None]]


@dataclass(frozen=True, init=False)
class Operation:
    """Single schema authority; pure handlers are synchronous, effectful async.

    Schema bytes are immutable. Every public schema projection is a new copy.
    Host assembly supplies scoped handler closures; no global service lookup.
    """

    name: str
    description: str
    effects: frozenset[Effect]
    handler: Handler | AsyncHandler
    retry_safe: bool
    recover: RecoverHandler | None
    default_timeout: float | None
    timeout_grace: float
    _input_json: str
    _output_json: str

    def __init__(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        output_schema: dict[str, Any] | bool,
        handler: Handler | AsyncHandler,
        *,
        effects: frozenset[Effect] = frozenset(),
        retry_safe: bool = False,
        recover: RecoverHandler | None = None,
        default_timeout: float | None = None,
        timeout_grace: float = 0,
    ) -> None:
        if len(name) > 64 or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", name):
            raise DefinitionError("operation name must be a DSL/Tool identifier")
        if (
            name in {"nil", "true", "false"}
            or name in SPECIAL_FORMS
            or name in CORE_BUILTINS
            or name in ARITHMETIC_BUILTINS
        ):
            raise DefinitionError(f"operation name collides with language vocabulary: {name}")
        if effects - {"read", "write"}:
            raise DefinitionError("unknown operation effect")
        if bool(effects) != inspect.iscoroutinefunction(handler):
            raise DefinitionError("pure handlers must be synchronous; effectful handlers async")
        if default_timeout is not None and (
            type(default_timeout) not in (int, float)
            or not math.isfinite(default_timeout)
            or default_timeout <= 0
        ):
            raise DefinitionError("default_timeout must be a finite positive number")
        if (
            type(timeout_grace) not in (int, float)
            or not math.isfinite(timeout_grace)
            or timeout_grace < 0
        ):
            raise DefinitionError("timeout_grace must be a finite nonnegative number")
        object.__setattr__(self, "timeout_grace", timeout_grace)
        object.__setattr__(self, "default_timeout", default_timeout)
        object.__setattr__(self, "retry_safe", retry_safe)
        object.__setattr__(self, "recover", recover)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "description", description)
        object.__setattr__(self, "handler", handler)
        object.__setattr__(self, "effects", frozenset(effects))
        object.__setattr__(
            self, "_input_json", json.dumps(admit_schema(input_schema, input_schema=True))
        )
        object.__setattr__(self, "_output_json", json.dumps(admit_schema(output_schema)))

    @property
    def input_schema(self) -> dict[str, Any]:
        return cast(dict[str, Any], json.loads(self._input_json))

    @property
    def output_schema(self) -> dict[str, Any] | bool:
        return cast(dict[str, Any] | bool, json.loads(self._output_json))


class OperationRegistry:
    """Fixed member-scoped operation catalog; duplicate names are errors."""

    def __init__(self, operations: Iterable[Operation]) -> None:
        self._operations: dict[str, Operation] = {}
        for operation in operations:
            if operation.name in self._operations:
                raise DefinitionError(f"duplicate operation: {operation.name}")
            self._operations[operation.name] = operation

    def get(self, name: str) -> Operation:
        try:
            return self._operations[name]
        except KeyError as error:
            raise OperationError(f"unknown operation: {name}") from error

    def definitions(self) -> tuple[Operation, ...]:
        return tuple(self._operations.values())


class _InvocationLock(asyncio.Lock):
    """Reject identical recursive invocations instead of waiting on ourselves."""

    def __init__(self) -> None:
        super().__init__()
        self._owner: asyncio.Task[Any] | None = None

    async def __aenter__(self) -> None:
        if self._owner is asyncio.current_task() and self._owner is not None:
            raise OperationError("Recursive invocation has the same frozen identity")
        await super().__aenter__()
        self._owner = asyncio.current_task()

    async def __aexit__(self, *args: Any) -> None:
        self._owner = None
        await super().__aexit__(*args)


class OperationExecutor:
    """Shared input/output boundary, bound to a host-created call context.

    Each invocation may bind its own context without mutating the member scope.
    Domain handlers and their host services own effect submission and persistence.
    """

    _store_writers: ClassVar[weakref.WeakKeyDictionary[Any, MemberWriteGate]] = (
        weakref.WeakKeyDictionary()
    )

    _store_locks: ClassVar[
        weakref.WeakKeyDictionary[Any, weakref.WeakValueDictionary[Any, _InvocationLock]]
    ] = weakref.WeakKeyDictionary()

    def __init__(
        self,
        registry: OperationRegistry,
        context: CallContext,
        store: InvocationStore | None = None,
        *,
        default_timeout: float = 30,
        write_gate: MemberWriteGate | None = None,
    ) -> None:
        self.registry = registry
        self.context = context
        self.store = store
        if (
            type(default_timeout) not in (int, float)
            or not math.isfinite(default_timeout)
            or default_timeout <= 0
        ):
            raise DefinitionError("executor default_timeout must be a finite positive number")
        self.default_timeout = default_timeout
        registered = self._store_writers.get(store) if store is not None else None
        if write_gate is not None and registered is not None and write_gate is not registered:
            raise DefinitionError("A different member write gate is already bound to this store")
        writer = write_gate or registered or MemberWriteGate()
        if store is not None:
            self._store_writers[store] = writer
        self._writer = writer
        self._locks: weakref.WeakValueDictionary[Any, _InvocationLock]
        if store is None:
            self._locks = weakref.WeakValueDictionary()
        else:
            locks = self._store_locks.get(store)
            if locks is None:
                locks = weakref.WeakValueDictionary()
                self._store_locks[store] = locks
            self._locks = locks

    def bind(self, context: CallContext) -> "OperationExecutor":
        """Bind a nested host identity while retaining the same member authority."""
        bound = self._context(context)
        executor = OperationExecutor(
            self.registry,
            bound,
            self.store,
            default_timeout=self.default_timeout,
            write_gate=self._writer,
        )
        return executor

    def call_pure(self, name: str, arguments: Any, context: CallContext | None = None) -> Any:
        bound = self._context(context)
        operation = self.registry.get(name)
        if operation.effects:
            raise OperationError(f"{name}: effectful operation requires async invocation")
        inputs = validate(operation.input_schema, arguments)
        result = cast(Handler, operation.handler)(bound, inputs)
        if inspect.isawaitable(result):
            if inspect.iscoroutine(result):
                result.close()
            raise OperationError(f"{name}: pure handler returned an awaitable")
        return validate(operation.output_schema, result, output=True)

    @staticmethod
    def definition_id(operation: Operation) -> str:
        definition = {
            "name": operation.name,
            "input": operation.input_schema,
            "output": operation.output_schema,
            "effects": sorted(operation.effects),
            "retry_safe": operation.retry_safe,
            "recover": operation.recover is not None,
            "default_timeout": operation.default_timeout,
            "timeout_grace": operation.timeout_grace,
        }
        return hashlib.sha256(
            json.dumps(definition, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def _invocation(self, operation: Operation, inputs: Any, bound: CallContext) -> Invocation:
        assert self.store is not None
        return Invocation(
            call_id=bound.call_id,
            root_id=bound.run_id,
            path=bound.call_path or (bound.call_id,),
            name=operation.name,
            arguments_json=bound.arguments_json
            or json.dumps(inputs, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
            definition_id=self.definition_id(operation),
            build_id=self.store.build_id,
        )

    async def replay(self, name: str, arguments: Any, context: CallContext) -> tuple[bool, Any]:
        """Inspect committed history before a driver applies a fresh-call budget cap."""
        bound = self._context(context)
        operation = self.registry.get(name)
        inputs = validate(operation.input_schema, arguments)
        if self.store is None:
            return False, None
        invocation = self._invocation(operation, inputs, bound)
        await asyncio.to_thread(self.store.ensure_root, bound.run_id)
        outcome = await asyncio.to_thread(self.store.lookup_invocation, invocation)
        recovered = await self._recover(operation, inputs, bound, invocation, outcome)
        if recovered is not None:
            return True, self._restore(recovered)
        if outcome is None or (
            ("write" not in operation.effects or operation.retry_safe)
            and outcome.error_type == "UnknownOutcomeError"
        ):
            return False, None
        return True, self._restore(outcome)

    async def _recover(
        self,
        operation: Operation,
        inputs: dict[str, Any],
        bound: CallContext,
        invocation: Invocation,
        previous: InvocationOutcome | None,
    ) -> InvocationOutcome | None:
        if operation.recover is None or (
            previous is not None and previous.error_type != "UnknownOutcomeError"
        ):
            return None
        scope = bound.execution or current_execution()
        if scope is None:
            return None
        recovered = await operation.recover(
            replace(bound, execution=scope.child(f"operation:{bound.call_id}")), inputs
        )
        if recovered is None:
            return None
        if not isinstance(recovered, InvocationOutcome) or (
            not recovered.success and recovered.error_type != "RejectedError"
        ):
            raise OperationError("Recovery hook must return a committed success or known rejection")
        if recovered.success:
            validate(operation.output_schema, recovered.value, output=True)
        await self._commit(invocation, recovered, inputs)
        return recovered

    @staticmethod
    def _restore(outcome: InvocationOutcome) -> Any:
        if outcome.success:
            return outcome.value
        errors: dict[str, type[Exception]] = {
            cls.__name__: cls
            for cls in (
                OperationError,
                InputError,
                OutputError,
                RejectedError,
                UnknownOutcomeError,
                RecoveryMismatchError,
                BudgetExceededError,
                ExecutionDeadlineError,
            )
        }
        kind = errors.get(outcome.error_type, OperationError)
        if kind in (RejectedError, UnknownOutcomeError):
            raise kind(outcome.error_message, outcome.error_data or {})
        error = kind(outcome.error_message)
        if outcome.error_data is not None:
            error.data = outcome.error_data  # type: ignore[attr-defined]
        raise error

    @staticmethod
    def _unknown_data(operation: Operation, inputs: dict[str, Any], call_id: str) -> dict[str, Any]:
        data = {
            "call_id": call_id,
            "outcome": "unknown",
            "retry_safe": "write" not in operation.effects or operation.retry_safe,
        }
        if "request_id" in operation.input_schema.get("properties", {}):
            data["request_id"] = inputs.get("request_id") or call_id
        return data

    def _timeout(
        self, operation: Operation, inputs: dict[str, Any], scope: ExecutionScope | None
    ) -> float:
        seconds = (
            operation.default_timeout
            if operation.default_timeout is not None
            else self.default_timeout
        )
        if "timeout" in operation.input_schema.get("properties", {}) and "timeout" in inputs:
            seconds = inputs["timeout"]
        seconds += operation.timeout_grace
        remaining = scope.remaining_seconds() if scope is not None else None
        return min(seconds, remaining) if remaining is not None else seconds

    async def call(self, name: str, arguments: Any, context: CallContext | None = None) -> Any:
        bound = self._context(context)
        operation = self.registry.get(name)
        if not operation.effects:
            return self.call_pure(name, arguments, context)
        inputs = validate(operation.input_schema, arguments)
        bound = self._context(context)
        scope = bound.execution or current_execution()
        if self.store is None:
            timeout = self._timeout(operation, inputs, scope)
            operation_scope = (
                scope.child(f"operation:{bound.call_id}", deadline=time.time() + timeout)
                if scope
                else None
            )
            handler_started = False
            try:
                with bind_execution(operation_scope) if scope else nullcontext():
                    async with asyncio.timeout(timeout):
                        if operation_scope is not None:
                            await operation_scope.initialize()
                            operation_scope.check()
                        async with self._writer.hold("write" in operation.effects):
                            if operation_scope is not None:
                                operation_scope.check()
                            handler_started = True
                            value = validate(
                                operation.output_schema,
                                await cast(AsyncHandler, operation.handler)(
                                    replace(bound, execution=operation_scope), inputs
                                ),
                                output=True,
                            )
                            return value
            except ExecutionDeadlineError:
                raise
            except TimeoutError as error:
                if not handler_started:
                    raise ExecutionDeadlineError(
                        "Operation timeout exhausted before dispatch; operation was not executed"
                    ) from error
                raise UnknownOutcomeError(
                    "Operation timeout elapsed after dispatch; outcome unknown",
                    self._unknown_data(operation, inputs, bound.call_id),
                ) from error
        await asyncio.to_thread(self.store.ensure_root, bound.run_id)
        invocation = self._invocation(operation, inputs, bound)
        key = (invocation.root_id, invocation.path)
        lock = self._locks.get(key)
        if lock is None:
            lock = _InvocationLock()
            self._locks[key] = lock
        previous = await asyncio.to_thread(self.store.lookup_invocation, invocation)
        recovered = await self._recover(operation, inputs, bound, invocation, previous)
        if recovered is not None:
            return self._restore(recovered)
        if previous is not None and not (
            ("write" not in operation.effects or operation.retry_safe)
            and previous.error_type == "UnknownOutcomeError"
        ):
            return self._restore(previous)
        timeout = self._timeout(operation, inputs, scope)
        operation_scope = (
            scope.child(f"operation:{bound.call_id}", deadline=time.time() + timeout)
            if scope
            else None
        )
        intent_started = False
        begin_task: asyncio.Task[bool] | None = None
        handler_started = False
        try:
            with bind_execution(operation_scope) if scope else nullcontext():
                async with asyncio.timeout(timeout):
                    if operation_scope is not None:
                        await operation_scope.initialize()
                        operation_scope.check()
                    # All writers take this gate before a per-invocation lock, including nested calls.
                    async with self._writer.hold("write" in operation.effects), lock:
                        previous = await asyncio.to_thread(self.store.lookup_invocation, invocation)
                        if previous is not None and not (
                            ("write" not in operation.effects or operation.retry_safe)
                            and previous.error_type == "UnknownOutcomeError"
                        ):
                            return self._restore(previous)
                        if operation_scope is not None:
                            operation_scope.check()
                        begin_task = asyncio.create_task(
                            asyncio.to_thread(self.store.begin_invocation, invocation)
                        )
                        fresh = await asyncio.shield(begin_task)
                        intent_started = fresh
                        if not fresh and "write" in operation.effects and not operation.retry_safe:
                            raise UnknownOutcomeError(
                                "Previous operation dispatch has no recorded result; automatic retry refused",
                                self._unknown_data(operation, inputs, bound.call_id),
                            )
                        if operation_scope is not None:
                            operation_scope.check()
                        try:
                            handler_started = True
                            result = await cast(AsyncHandler, operation.handler)(
                                replace(bound, invocation=invocation, execution=operation_scope),
                                inputs,
                            )
                        except asyncio.CancelledError:
                            # Durable intent remains: cancellation cannot revoke an external effect.
                            raise
                        except (BudgetExceededError, ExecutionDeadlineError) as error:
                            committed = await self._committed(invocation)
                            if (
                                committed is not None
                                and committed.error_type != "UnknownOutcomeError"
                            ):
                                return self._restore(committed)
                            await self._commit(
                                invocation,
                                InvocationOutcome(
                                    False,
                                    error_type=type(error).__name__,
                                    error_message=str(error),
                                ),
                                inputs,
                            )
                            raise
                        except OperationError as error:
                            committed = await self._committed(invocation)
                            if (
                                committed is not None
                                and committed.error_type != "UnknownOutcomeError"
                            ):
                                return self._restore(committed)
                            outcome = InvocationOutcome(
                                False,
                                error_type="RejectedError"
                                if isinstance(error, RejectedError)
                                else type(error).__name__
                                if type(error)
                                in (
                                    OperationError,
                                    InputError,
                                    OutputError,
                                    RejectedError,
                                    UnknownOutcomeError,
                                    RecoveryMismatchError,
                                )
                                else "OperationError",
                                error_message=str(error),
                                error_data={
                                    **self._unknown_data(operation, inputs, bound.call_id),
                                    **error.data,
                                }
                                if isinstance(error, UnknownOutcomeError)
                                else getattr(error, "data", None),
                            )
                            await self._commit(invocation, outcome, inputs)
                            return self._restore(outcome)
                        except Exception as error:
                            committed = await self._committed(invocation)
                            if (
                                committed is not None
                                and committed.error_type != "UnknownOutcomeError"
                            ):
                                return self._restore(committed)
                            unknown = UnknownOutcomeError(
                                str(error),
                                self._unknown_data(operation, inputs, bound.call_id),
                            )
                            await self._commit(
                                invocation,
                                InvocationOutcome(
                                    False,
                                    error_type="UnknownOutcomeError",
                                    error_message=str(unknown),
                                    error_data=unknown.data,
                                ),
                                inputs,
                            )
                            raise unknown from error
                        try:
                            value = validate(operation.output_schema, result, output=True)
                        except OperationError as error:
                            try:
                                raw = json_value(result)
                            except InputError:
                                raw = {"type": type(result).__name__, "repr": repr(result)}
                            await self._commit(
                                invocation,
                                InvocationOutcome(
                                    False,
                                    error_type="OutputError",
                                    error_message=str(error),
                                    raw_result=raw,
                                ),
                                inputs,
                            )
                            raise
                        await self._commit(invocation, InvocationOutcome(True, value=value), inputs)
                        return value
        except asyncio.CancelledError:
            # Cancellation still reaches the task owner. Reconcile any domain-atomic
            # receipt first; an interrupted caller cannot revoke a committed effect.
            if begin_task is not None:
                # Database failure cannot replace the caller's cancellation.
                with suppress(Exception):
                    await self._finish_begin(begin_task)
            if handler_started:
                reconciliation = asyncio.create_task(self._committed(invocation))
                while not reconciliation.done():
                    try:
                        await asyncio.shield(reconciliation)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
            raise
        except TimeoutError as error:
            if begin_task is not None:
                intent_started = await self._finish_begin(begin_task)
            committed = await self._committed(invocation)
            if committed is not None and committed.error_type != "UnknownOutcomeError":
                return self._restore(committed)
            if not handler_started and begin_task is not None and not intent_started:
                raise UnknownOutcomeError(
                    "Previous operation dispatch has no recorded result; automatic retry refused",
                    self._unknown_data(operation, inputs, bound.call_id),
                ) from error
            if not handler_started:
                rejected = ExecutionDeadlineError(
                    "Operation timeout exhausted before dispatch; operation was not executed"
                )
                if intent_started:
                    await self._commit(
                        invocation,
                        InvocationOutcome(
                            False, error_type="ExecutionDeadlineError", error_message=str(rejected)
                        ),
                        inputs,
                    )
                raise rejected from error
            unknown = UnknownOutcomeError(
                "Operation timeout elapsed after dispatch; outcome unknown",
                self._unknown_data(operation, inputs, bound.call_id),
            )
            await self._commit(
                invocation,
                InvocationOutcome(
                    False,
                    error_type="UnknownOutcomeError",
                    error_message=str(unknown),
                    error_data=unknown.data,
                ),
                inputs,
            )
            raise unknown from error

    @staticmethod
    async def _finish_begin(task: asyncio.Task[bool]) -> bool:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
        return task.result()

    async def _committed(self, invocation: Invocation) -> InvocationOutcome | None:
        assert self.store is not None
        try:
            outcome = await asyncio.to_thread(self.store.lookup_invocation, invocation)
            return outcome
        except RecoveryMismatchError:
            raise
        except Exception:
            # An unavailable receipt does not establish failure or undo an effect.
            return None

    async def _commit(
        self, invocation: Invocation, outcome: InvocationOutcome, inputs: dict[str, Any]
    ) -> None:
        assert self.store is not None
        try:
            await asyncio.to_thread(self.store.commit_invocation, invocation, outcome)
        except Exception as error:
            committed = await self._committed(invocation)
            if committed is not None and committed == outcome:
                return
            raise UnknownOutcomeError(
                "Operation result could not be durably recorded",
                self._unknown_data(
                    self.registry.get(invocation.name),
                    inputs,
                    invocation.call_id,
                ),
            ) from error

    def _context(self, context: CallContext | None) -> CallContext:
        bound = context or self.context
        if bound.member_id != self.context.member_id:
            raise OperationError("operation context belongs to another member")
        return bound
