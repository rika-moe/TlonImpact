"""Sample public registry reads through the shared invocation journal."""

from typing import Any, Protocol

from tlon.operations.core import CallContext, Operation
from tlon.operations.schema import OperationError


class RegistryQueryPort(Protocol):
    """Public registry read ports, bound by the member assembly."""

    def is_sleeping(self, agent_id: Any) -> bool:
        """Whether the registry holds an explicit sleep intent."""
        ...

    def is_running(self, agent_id: Any) -> bool:
        """Currently in the run table."""
        ...

    def is_lazy(self, agent_id: Any) -> bool:
        """A member that can sleep / be drained: no boot handler.

        Unified member model (ADR 0006): laziness is spec-derived (a
        missing or unreadable spec also counts), never a birth marker."""
        ...

    def member_roster(self) -> str:
        """Formatted public delivery directory, including offline members."""
        ...


def make_registry_query_operations(host: RegistryQueryPort | None = None) -> tuple[Operation, ...]:
    """Bind registry reads; each call uses named arguments and durable receipts."""

    def bound() -> RegistryQueryPort:
        if host is None:
            raise OperationError("registry queries are not bound")
        return host

    async def sleeping(context: CallContext, args: dict[str, Any]) -> bool:
        return bound().is_sleeping(args["agent_id"])

    async def running(context: CallContext, args: dict[str, Any]) -> bool:
        return bound().is_running(args["agent_id"])

    async def lazy(context: CallContext, args: dict[str, Any]) -> bool:
        return bound().is_lazy(args["agent_id"])

    async def roster(context: CallContext, args: dict[str, Any]) -> str:
        return bound().member_roster()

    member = {
        "type": "object",
        "properties": {"agent_id": {"type": "string"}},
        "required": ["agent_id"],
        "additionalProperties": False,
    }
    empty = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
    return (
        Operation(
            "member-sleeping",
            "查询成员是否休眠。",
            member,
            {"type": "boolean"},
            sleeping,
            effects=frozenset({"read"}),
        ),
        Operation(
            "member-running",
            "查询成员是否正在运行。",
            member,
            {"type": "boolean"},
            running,
            effects=frozenset({"read"}),
        ),
        Operation(
            "member-lazy",
            "查询成员是否允许空闲卸载。",
            member,
            {"type": "boolean"},
            lazy,
            effects=frozenset({"read"}),
        ),
        Operation(
            "member-roster",
            "读取格式化的公开成员寻址黄页。",
            empty,
            {"type": "string"},
            roster,
            effects=frozenset({"read"}),
        ),
    )
