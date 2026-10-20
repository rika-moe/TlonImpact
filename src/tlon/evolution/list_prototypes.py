"""List behavior prototypes service."""

from collections.abc import Callable, Sequence
from typing import Any

from tlon.evolution.ports import PrototypeSummary


class PrototypeCatalog:
    """Service to list available behavior prototypes.

    Reads ``~/.tlon/prototypes/`` (the seeded, user-editable library),
    so prototypes added or modified by the user are visible to agents.
    The actual listing is injected as ``lister`` (runtime providers are injected).
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
                "trusted": proto.trusted,
                "parent": proto.parent,
                "generation": proto.generation,
            }
            for proto in prototypes
        ]
        if not data:
            return []
        return data
