"""Write an LLM-authored behavior prototype to the staging area (stage 4a).

All rules use one event-driven program format
(phase C). Unified member model (ADR 0006): prototypes carry no persona —
a member's persona files are seeded at creation and member-owned
afterwards (self-editable via ``persona-update``, ADR 0024), never part of
the behavior genome.
"""

from collections.abc import Callable
from typing import Any

from tlon.evolution.ports import PrototypeError, PrototypeSummary
from tlon.operations.schema import RejectedError

_BEHAVIOR_HINT = "Read tlon/docs/README.md and use program-check before prototype-write."


def require_bundle(behavior: Any, modules: Any) -> tuple[str, dict[str, str]]:
    """Machine authors submit complete v2 sources; staging validates linking."""
    from tlosure.edn import loads

    if not isinstance(behavior, str) or not isinstance(modules, dict) or not modules:
        raise RejectedError(
            "完整 v2 程序包需要 behavior 字符串及非空 modules 对象", {"hint": _BEHAVIOR_HINT}
        )
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in modules.items()):
        raise RejectedError("modules 必须是路径到源码字符串的对象", {})
    try:
        version = loads(behavior).get("version")
    except (ValueError, AttributeError) as error:
        raise RejectedError("behavior 必须是 v2 EDN map", {}) from error
    if version != 2:
        raise RejectedError("机器提案只接受 v2 程序包", {"hint": _BEHAVIOR_HINT})
    return behavior, dict(modules)


class PrototypeWriter:
    """Service to author a new behavior prototype (pure-data spec).

    Accepts a complete v2 behavior.tlon and module source package. Prototypes carry no persona (unified member
    model, ADR 0006). The prototype lands in
    ``~/.tlon/prototypes-staging/`` marked untrusted: it cannot be spawned
    until a human approves it (ops/WebUI — the Gatekeeper model of
    stage 4a). Validation errors come back verbatim so the LLM can fix
    its EDN and retry.
    """

    def __init__(
        self,
        author_id: str,
        writer: Callable[..., PrototypeSummary],
    ) -> None:
        self._author_id = author_id
        self._writer = writer

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        if "name" in kwargs:
            raise RejectedError("name is system-generated; provide display_name instead", {})
        if not isinstance(kwargs.get("display_name"), str) or not kwargs["display_name"].strip():
            raise RejectedError("display_name must be a non-empty behavior label", {})

        behavior, modules = require_bundle(kwargs.get("behavior"), kwargs.get("modules"))
        display_name: str = kwargs["display_name"]
        description: str = kwargs.get("description", "")
        parent: str | None = kwargs.get("parent") or None
        proposal_note: str | None = kwargs.get("proposal_note") or None
        lessons: str | None = kwargs.get("lessons") or None

        hint = _BEHAVIOR_HINT
        try:
            info = self._writer(
                behavior,
                modules=modules,
                display_name=display_name,
                description=description,
                author_id=self._author_id,
                parent=parent,
                proposal_note=proposal_note,
                lessons=lessons,
            )
        except PrototypeError as e:
            raise RejectedError(str(e), {"hint": hint}) from e

        data: dict[str, Any] = {
            "name": info.name,
            "display_name": info.display_name,
            "trusted": info.trusted,
            "parent": info.parent,
            "generation": info.generation,
            "status": "pending_approval",
        }
        return data
