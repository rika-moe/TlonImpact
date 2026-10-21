"""Host execution scopes and durable model allowances, independent of runtime layers."""

import asyncio
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, cast


class MemberWriteGate:
    """One member's writer boundary, reentrant only within its owning task."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._owner: asyncio.Task[Any] | None = None

    @asynccontextmanager
    async def hold(self, write: bool = True) -> AsyncIterator[None]:
        task = asyncio.current_task()
        if not write:
            yield
            return
        if task is not None and self._owner is task:
            scope = current_execution()
            if scope is not None:
                scope.check()
            yield
            return
        async with self._lock:
            scope = current_execution()
            if scope is not None:
                scope.check()
            self._owner = task
            try:
                yield
            finally:
                self._owner = None


class BudgetExceededError(RuntimeError):
    """No unreserved allowance remains in the owning execution root."""


class ExecutionDeadlineError(TimeoutError):
    """The inherited wall-clock deadline has elapsed."""


class UnknownModelOutcomeError(RuntimeError):
    """A dispatched model request has no durable response; do not resend it."""


class ModelRequestRejectedError(RuntimeError):
    """A provider explicitly rejected a request without generating a completion."""

    def __init__(self, message: str, error_type: str = "ModelRequestRejectedError") -> None:
        super().__init__(message)
        self.error_type = error_type


class ExecutionStore(Protocol):
    def freeze_model_context(
        self, root_id: str, slot: str, value: dict[str, Any]
    ) -> dict[str, Any]: ...
    def load_model_context(self, root_id: str, slot: str) -> dict[str, Any] | None: ...
    def lookup_model_path(self, root_id: str, path: tuple[str, ...]) -> dict[str, Any] | None: ...
    def ensure_budget(
        self, root_id: str, token_limit: int, measure: str, deadline: float | None = None
    ) -> dict[str, Any]: ...
    def peek_model(
        self, root_id: str, path: tuple[str, ...], request_json: str
    ) -> dict[str, Any] | None: ...
    def reserve_model(
        self,
        root_id: str,
        path: tuple[str, ...],
        request_json: str,
        output_limit: int,
        estimated_input: int,
    ) -> dict[str, Any]: ...
    def commit_model(
        self, root_id: str, path: tuple[str, ...], response: dict[str, Any], actual: int | None
    ) -> dict[str, Any]: ...
    def reject_model(
        self, root_id: str, path: tuple[str, ...], error_type: str, message: str
    ) -> None: ...
    def budget_usage(self, root_id: str) -> dict[str, Any]: ...


_CURRENT: ContextVar["ExecutionScope | None"] = ContextVar("tlon_execution", default=None)


def current_execution() -> "ExecutionScope | None":
    return _CURRENT.get()


@contextmanager
def bind_execution(scope: "ExecutionScope | None") -> Iterator[None]:
    token = _CURRENT.set(scope)
    try:
        yield
    finally:
        _CURRENT.reset(token)


@dataclass
class ExecutionScope:
    store: ExecutionStore
    root_id: str
    token_limit: int
    measure: Literal["output", "total"]
    path: tuple[str, ...] = ()
    deadline: float | None = None
    cancelled: asyncio.Event = field(default_factory=asyncio.Event)
    parent: "ExecutionScope | None" = None
    _model_ordinal: int = field(default=0, init=False, repr=False)

    def next_model_path(self) -> tuple[str, ...]:
        """Inspect the next model identity without reserving or advancing it."""
        return (*self.path, f"model:{self._model_ordinal}")

    def child(self, segment: str, *, deadline: float | None = None) -> "ExecutionScope":
        return ExecutionScope(
            self.store,
            self.root_id,
            self.token_limit,
            self.measure,
            (*self.path, segment),
            self.deadline if deadline is None else deadline,
            asyncio.Event(),
            self,
        )

    def cancel(self) -> None:
        self.cancelled.set()

    def effective_deadline(self) -> float | None:
        inherited = self.parent.effective_deadline() if self.parent is not None else None
        if inherited is None:
            return self.deadline
        return min(self.deadline, inherited) if self.deadline is not None else inherited

    def remaining_seconds(self) -> float | None:
        deadline = self.effective_deadline()
        return deadline - time.time() if deadline is not None else None

    async def initialize(self) -> None:
        # Per-call deadlines narrow execution, not the frozen budget contract.
        # A child sharing its parent's budget initializes that owner only.
        if (
            self.parent is not None
            and self.store is self.parent.store
            and self.root_id == self.parent.root_id
        ):
            if self.token_limit != self.parent.token_limit or self.measure != self.parent.measure:
                raise ValueError("A child cannot change its inherited budget")
            await self.parent.initialize()
            return
        budget = await asyncio.to_thread(
            self.store.ensure_budget,
            self.root_id,
            self.token_limit,
            self.measure,
            self.effective_deadline(),
        )
        self.deadline = budget["deadline"]

    def check(self) -> None:
        if self.parent is not None:
            self.parent.check()
        if self.cancelled.is_set():
            raise asyncio.CancelledError()
        remaining = self.remaining_seconds()
        if remaining is not None and remaining <= 0:
            raise ExecutionDeadlineError("Execution deadline exhausted")

    async def model_call(
        self,
        request: dict[str, Any],
        output_limit: int,
        execute: Callable[[int], Awaitable[dict[str, Any]]],
        *,
        estimated_input: int | None = None,
    ) -> dict[str, Any]:
        ordinal = self._model_ordinal
        self._model_ordinal += 1
        path = (*self.path, f"model:{ordinal}")
        encoded = json.dumps(
            {"request": request, "output_limit": output_limit},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        # Callers may use the configured model's tokenizer; this byte estimate remains
        # a conservative admission heuristic, never a provider token upper bound.
        if estimated_input is None:
            estimated_input = len(
                json.dumps(request.get("messages", []), ensure_ascii=False).encode()
            ) + len(json.dumps(request.get("tools"), ensure_ascii=False).encode())
        await self.initialize()
        reservation = await asyncio.to_thread(self.store.peek_model, self.root_id, path, encoded)
        if reservation is None:
            self.check()
            reservation = await asyncio.to_thread(
                self.store.reserve_model, self.root_id, path, encoded, output_limit, estimated_input
            )
        if reservation["status"] == "replay":
            return cast(dict[str, Any], reservation["response"])
        if reservation["status"] == "rejected":
            raise ModelRequestRejectedError(reservation["message"], reservation["error_type"])
        if reservation["status"] == "unknown":
            raise UnknownModelOutcomeError(f"Model request outcome unknown: {self.root_id}/{path}")
        self.check()
        try:
            remaining = self.remaining_seconds()
            async with asyncio.timeout(remaining):
                response = await execute(reservation["output_limit"])
        except ModelRequestRejectedError as error:
            await asyncio.to_thread(
                self.store.reject_model, self.root_id, path, error.error_type, str(error)
            )
            raise
        except asyncio.CancelledError:
            raise
        except Exception as error:
            raise UnknownModelOutcomeError(
                f"Model request outcome unknown: {self.root_id}/{path}: {error}"
            ) from error
        usage = response.get("usage")
        actual = None
        if isinstance(usage, dict):
            if self.measure == "output":
                amount = usage.get("completion_tokens")
                if type(amount) is int and amount >= 0:
                    actual = amount
            else:
                amount = usage.get("total_tokens")
                if type(amount) is int and amount >= 0:
                    actual = amount
                elif all(
                    type(usage.get(key)) is int and usage[key] >= 0
                    for key in ("prompt_tokens", "completion_tokens")
                ):
                    actual = usage["prompt_tokens"] + usage["completion_tokens"]
        try:
            return await asyncio.to_thread(
                self.store.commit_model, self.root_id, path, response, actual
            )
        except Exception as error:
            try:
                committed = await asyncio.to_thread(
                    self.store.peek_model, self.root_id, path, encoded
                )
            except Exception:
                committed = None
            if committed is not None and committed["status"] == "replay":
                return cast(dict[str, Any], committed["response"])
            raise UnknownModelOutcomeError(
                "Model response could not be durably committed"
            ) from error
