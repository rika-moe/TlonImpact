"""Prototype lineage query service — the genealogy as first-class data ()."""

from collections.abc import Callable
from typing import Any

from tlon.evolution.ports import PrototypeError
from tlon.operations.schema import RejectedError


class PrototypeLineage:
    """Service to walk a prototype's ``:parent`` chain up to the root.

    The lineage is derived, never stored: the injected ``lineage`` callable
    (``agents.prototypes.get_prototype_lineage``, : tools must not
    import ``tlon.agents``) reads each generation's own metadata and diffs
    adjacent ``behavior.tlon`` texts. A deleted/renamed parent marks the
    oldest reachable generation ``broken``.
    """

    def __init__(self, lineage: Callable[[str], list[dict[str, Any]]]) -> None:
        self._lineage = lineage

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        name: str = kwargs["name"]
        try:
            chain = self._lineage(name)
        except PrototypeError as e:
            raise RejectedError(str(e), {}) from e
        return {"lineage": chain}
