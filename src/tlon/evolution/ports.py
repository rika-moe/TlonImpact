"""Evolution dependency ports shared by runtime providers and member services.

The assembler binds catalog, staging and review providers. These summaries
expose metadata without importing the member runtime into service contracts.
"""

from typing import Protocol


class PrototypeError(ValueError):
    """Prototype lookup or instantiation failure."""


class PrototypeSummary(Protocol):
    """Read-only view of prototype metadata used by evolution services.

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

    @property
    def parent(self) -> str | None: ...

    @property
    def generation(self) -> int: ...

    @property
    def proposal_note(self) -> str | None: ...
