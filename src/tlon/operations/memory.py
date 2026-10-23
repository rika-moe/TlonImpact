"""Shared memory and session read contracts, bound to member-owned services."""

from typing import Any, Protocol

from tlon.base.execution import (
    BudgetExceededError,
    ExecutionDeadlineError,
    ModelRequestRejectedError,
)
from tlon.memory.session_digest import render_session_digest
from tlon.operations.core import CallContext, Operation
from tlon.operations.schema import OperationError, RejectedError, UnknownOutcomeError


def _object(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


class MemoryPort(Protocol):
    agent_id: str

    async def remember_fact(self, fact: str, importance: float = 0.5) -> str: ...
    async def search_memories(self, query: str, limit: int = 5) -> str: ...
    async def forget(self, query: str, confirm: bool = False) -> str: ...


class SessionReadPort(Protocol):
    async def read_events(self, session_id: str) -> list[dict[str, Any]]: ...


def make_memory_operations(memory: MemoryPort, logs: SessionReadPort) -> tuple[Operation, ...]:
    """Keep existing retrieval/conflict semantics; expose no owner override."""

    async def remember(context: CallContext, args: dict[str, Any]) -> dict[str, Any]:
        check(context)
        try:
            return {
                "claim_id": await memory.remember_fact(args["fact"], args.get("importance", 0.5))
            }
        except (OperationError, BudgetExceededError, ExecutionDeadlineError):
            raise
        except ModelRequestRejectedError as error:
            raise RejectedError(str(error), {"reason": error.error_type}) from error
        except Exception as error:
            raise unknown(context, error) from error

    async def search(context: CallContext, args: dict[str, Any]) -> dict[str, Any]:
        check(context)
        return {"results": await memory.search_memories(args["query"], args.get("limit", 5))}

    async def forget(context: CallContext, args: dict[str, Any]) -> dict[str, Any]:
        check(context)
        try:
            return {"result": await memory.forget(args["query"], args.get("confirm", False))}
        except (OperationError, BudgetExceededError, ExecutionDeadlineError):
            raise
        except ModelRequestRejectedError as error:
            raise RejectedError(str(error), {"reason": error.error_type}) from error
        except Exception as error:
            if args.get("confirm", False):
                raise unknown(context, error) from error
            raise

    async def read(context: CallContext, args: dict[str, Any]) -> dict[str, Any]:
        check(context)
        session_id = args["session_id"]
        try:
            events = await logs.read_events(session_id)
        except Exception as error:
            raise OperationError(str(error)) from error
        if not events:
            raise RejectedError(f"session not found: {session_id}", {"outcome": "rejected"})
        return {
            "session_id": session_id,
            "digest": render_session_digest(events),
            "event_count": len(events),
        }

    def check(context: CallContext) -> None:
        if context.member_id != memory.agent_id:
            raise OperationError("memory manager belongs to another member")

    def unknown(context: CallContext, error: Exception) -> UnknownOutcomeError:
        return UnknownOutcomeError(
            str(error), {"outcome": "unknown", "call_id": context.call_id, "retry_safe": False}
        )

    text = {"type": "string"}
    query = {"type": "string", "minLength": 1}
    return (
        Operation(
            "memory-remember",
            "记录一条重要记忆，保留现有冲突判断。",
            _object(
                {"fact": query, "importance": {"type": "number", "minimum": 0, "maximum": 1}},
                ["fact"],
            ),
            _object({"claim_id": text}, ["claim_id"]),
            remember,
            effects=frozenset({"write"}),
        ),
        Operation(
            "memory-search",
            "搜索自己的长期记忆，来源会话可用 session-read 回读。",
            _object(
                {"query": query, "limit": {"type": "integer", "minimum": 1, "maximum": 100}},
                ["query"],
            ),
            _object({"results": text}, ["results"]),
            search,
            effects=frozenset({"read"}),
        ),
        Operation(
            "memory-forget",
            "预览匹配记忆，confirm 为 true 时删除。",
            _object({"query": query, "confirm": {"type": "boolean"}}, ["query"]),
            _object({"result": text}, ["result"]),
            forget,
            effects=frozenset({"write"}),
        ),
        Operation(
            "session-read",
            "回读自己的交互来源，旧 session ID 或 program:<run_id>，返回有界摘要；只读且不恢复执行。",
            _object({"session_id": query}, ["session_id"]),
            _object(
                {"session_id": text, "digest": text, "event_count": {"type": "integer"}},
                ["session_id", "digest", "event_count"],
            ),
            read,
            effects=frozenset({"read"}),
        ),
    )


def memory_operation_definitions() -> tuple[Operation, ...]:
    """Inspect definitions without constructing a filesystem-backed manager."""
    return make_memory_operations(_UnboundMemory(), _UnboundSessions())


class _UnboundMemory:
    agent_id = ""

    async def remember_fact(self, fact: str, importance: float = 0.5) -> str:
        raise OperationError("memory service is not bound")

    async def search_memories(self, query: str, limit: int = 5) -> str:
        raise OperationError("memory service is not bound")

    async def forget(self, query: str, confirm: bool = False) -> str:
        raise OperationError("memory service is not bound")


class _UnboundSessions:
    async def read_events(self, session_id: str) -> list[dict[str, Any]]:
        raise OperationError("session journal is not bound")
