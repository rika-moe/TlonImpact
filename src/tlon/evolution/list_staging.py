"""List staging prototypes service — proposals awaiting human approval ()."""

from collections.abc import Callable, Sequence
from typing import Any

from tlon.evolution.ports import PrototypeSummary


class StagingCatalog:
    """Service to list prototypes awaiting human trust approval.

    Reads ``~/.tlon/prototypes-staging/`` — the untrusted staging area
    where LLM-authored prototypes land. The actual listing is injected
    as ``lister`` (: tools must not import ``tlon.agents``).
    """

    def __init__(self, lister: Callable[[], Sequence[PrototypeSummary]]) -> None:
        self._lister = lister

    async def execute(self, **kwargs: Any) -> list[dict[str, Any]]:
        prototypes = self._lister()
        data = [
            {
                "name": proto.name,
                "display_name": proto.display_name,
                "description": proto.description,
                "author": proto.author,
                "parent": proto.parent,
                "generation": proto.generation,
            }
            for proto in prototypes
        ]
        if not data:
            return []
        return data
