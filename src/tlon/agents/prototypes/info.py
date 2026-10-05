"""Read-side prototype infrastructure: file-name constants, PrototypeInfo,
directory qualification/lookup, metadata/parameter readers, and the shared
name/lineage-parent guards — the base every other prototypes module uses.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tlon.agents.behavior_template import ParameterSpec, analyze
from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths
from tlon.tools.prototypes.ports import PrototypeError
from tlosure.edn import loads

logger = get_logger(__name__)

PROTOTYPE_PACKAGE = "tlon.prototypes"
METADATA_FILE = "prototype.tlon"
BEHAVIOR_FILE = "behavior.tlon"
LESSONS_FILE = "LESSONS.md"
REVIEWS_FILE = "REVIEW.md"


@dataclass(frozen=True)
class PrototypeInfo:
    """Metadata of one behavior prototype.

    ``trusted`` defaults to True for user-installed prototypes (handwritten
    implies trusted); only the staging area writes ``:trusted false``.
    ``runtime`` is **derived** from the behavior file's rules (phase C: any
    rule with a ``:loop`` ⇒ ``"react"``, else ``"script"``) — it is never a
    metadata keyword anymore. ``parameters`` (phase F) is the template's
    declared ``:parameters`` (empty for a concrete behavior), so callers
    can prompt for values before instantiation.

    Lineage (see docs/design/prototypes.md): ``parent`` names the
    prototype this one was derived from (None = root), ``generation`` is
    1 for roots and the
    parent's generation + 1 otherwise, ``proposal_note`` is the free-text
    rationale for this generation's change. Files predating the field read
    as roots (parent=None, generation=1) — no migration.
    """

    name: str
    display_name: str
    description: str
    trusted: bool = True
    author: str | None = None
    parent: str | None = None
    generation: int = 1
    proposal_note: str | None = None
    parameters: tuple[ParameterSpec, ...] = ()

    @property
    def has_parameters(self) -> bool:
        """Whether instantiation requires template parameter values."""
        return bool(self.parameters)


def _is_prototype_dir(proto_dir: Path) -> bool:
    """A directory qualifies when it carries the unified behavior file."""
    return (proto_dir / BEHAVIOR_FILE).exists()


def _find_prototype_dir(name: str, p: TlonPaths) -> Path | None:
    """Locate a prototype by name in either area (trusted first, then staging)."""
    for root in (p.prototypes_dir(), p.prototypes_staging_dir()):
        proto_dir = root / name
        if _is_prototype_dir(proto_dir):
            return proto_dir
    return None


def _validate_name(name: str) -> None:
    """Prototype names share the AgentConfig charset: alnum plus ``-``/``_``."""
    if not name or not name.replace("-", "").replace("_", "").isalnum():
        raise PrototypeError(
            f"invalid prototype name {name!r}: only letters, digits, '-' and '_' allowed"
        )


def _resolve_parent_generation(parent: str, p: TlonPaths) -> int:
    """Validate a lineage parent and derive the child's generation.

    The parent may live in either area (trusted or staging) — a staged
    proposal can be the parent of a further proposal.

    Raises:
        PrototypeError: invalid parent name or parent not found
    """
    _validate_name(parent)
    parent_dir = _find_prototype_dir(parent, p)
    if parent_dir is None:
        raise PrototypeError(f"parent prototype '{parent}' not found")
    return _read_metadata(parent_dir).generation + 1


def _read_parameters(proto_dir: Any) -> tuple[ParameterSpec, ...]:
    """The behavior template's declared ``:parameters`` (empty if concrete).

    Metadata reading must never fail a listing: an unparseable behavior
    yields no parameters.
    """
    behavior_file = proto_dir / BEHAVIOR_FILE
    if not behavior_file.exists():
        return ()
    try:
        return analyze(behavior_file.read_text(encoding="utf-8")).parameters
    except Exception as e:
        logger.warning("prototype_parameters_unreadable", path=str(behavior_file), error=str(e))
        return ()


def _read_metadata(proto_dir: Any) -> PrototypeInfo:
    """Read ``prototype.tlon`` metadata, falling back to the directory name.

    No runtime is derived or declared (ADR 0006, unified member model):
    a prototype is a behavior template, not a member class — whether a
    spawned member gets the cognitive engine follows from its spec.
    """
    name = proto_dir.name
    parameters = _read_parameters(proto_dir)
    metadata_file = proto_dir / METADATA_FILE
    if not metadata_file.exists():
        return PrototypeInfo(
            name=name,
            display_name=name,
            description="",
            parameters=parameters,
        )
    try:
        data = loads(metadata_file.read_text(encoding="utf-8"))
        author = data.get("author")
        parent = data.get("parent")
        note = data.get("proposal-note")
        generation = data.get("generation")
        return PrototypeInfo(
            name=str(data.get("name") or name),
            display_name=str(data.get("display-name") or data.get("display_name") or name),
            description=str(data.get("description") or ""),
            trusted=bool(data.get("trusted", True)),
            author=str(author) if author else None,
            parent=str(parent) if parent else None,
            generation=generation
            if isinstance(generation, int) and not isinstance(generation, bool)
            else 1,
            proposal_note=str(note) if note else None,
            parameters=parameters,
        )
    except Exception as e:
        logger.warning("prototype_metadata_invalid", path=str(metadata_file), error=str(e))
        return PrototypeInfo(name=name, display_name=name, description="")
