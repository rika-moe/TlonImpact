"""Durable leaf-call identities and outcomes, independent of either driver."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from tlosure.builtins import Builtin


@dataclass(frozen=True)
class Invocation:
    call_id: str
    root_id: str
    path: tuple[str, ...]
    name: str
    arguments_json: str
    definition_id: str
    build_id: str


@dataclass(frozen=True)
class InvocationOutcome:
    success: bool
    value: Any = None
    error_type: str = ""
    error_message: str = ""
    error_data: Any = None
    raw_result: Any = None


class InvocationStore(Protocol):
    @property
    def build_id(self) -> str: ...
    def ensure_root(self, root_id: str) -> None: ...
    def freeze_definitions(
        self, root_id: str, definitions: dict[str, dict[str, Any]]
    ) -> dict[str, dict[str, Any]]: ...
    def load_definitions(self, root_id: str) -> dict[str, dict[str, Any]] | None: ...
    def lookup_invocation(self, invocation: Invocation) -> InvocationOutcome | None: ...
    def begin_invocation(self, invocation: Invocation) -> bool: ...
    def commit_invocation(self, invocation: Invocation, outcome: InvocationOutcome) -> None: ...


def registry_identity(builtins: dict[str, "Builtin"]) -> str:
    """Freeze host vocabulary and build; composite artifacts belong to individual roots."""
    import hashlib
    import json

    from tlon.base.runtime_identity import runtime_identity
    from tlosure.edn import dumps

    definitions = {
        name: {
            "pure": builtin.pure,
            "params": builtin.closure.params if builtin.closure else None,
            "rest": builtin.closure.rest if builtin.closure else None,
            "body": dumps(builtin.closure.body) if builtin.closure else None,
            "env": dumps(builtin.closure.env) if builtin.closure else None,
        }
        for name, builtin in sorted(builtins.items())
    }
    payload = json.dumps([runtime_identity(), definitions], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()
