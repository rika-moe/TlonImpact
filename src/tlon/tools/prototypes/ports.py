"""Ports for the prototype tools (dependency inversion, ).

``PrototypeError`` is raised by the prototype library
(``tlon.agents.prototypes``) and caught by the tools; declaring it here
keeps ``tlon.tools`` free of any ``tlon.agents`` import. ``PrototypeSummary``
is the read-only metadata view the list/write tools consume —
``agents.prototypes.PrototypeInfo`` satisfies it structurally, and the
assembler injects ``list_prototypes`` / ``write_staging_prototype`` as
plain callables.
"""

from typing import Protocol


class PrototypeError(ValueError):
    """Prototype lookup or instantiation failure."""


class PrototypeSummary(Protocol):
    """Read-only view of a prototype's metadata used by the tools.

    No runtime is declared or derived (unified member model, ADR 0006):
    a prototype is a behavior template, not a member class.
    """

    @property
    def name(self) -> str: ...

    @property
    def display_name(self) -> str: ...

    @property
    def description(self) -> str: ...

    @property
    def trusted(self) -> bool: ...

    @property
    def author(self) -> str | None: ...
