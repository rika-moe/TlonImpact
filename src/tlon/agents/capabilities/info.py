"""Read-side capability infrastructure: file-name constants,
CapabilityInfo, directory qualification, and the metadata/artifact
readers — the base every other capabilities module uses.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tlon.agents.capabilities.spec import CapabilityError, CapabilitySpec
from tlon.base.logger import get_logger
from tlosure.edn import loads

logger = get_logger(__name__)

METADATA_FILE = "capability.edn"
CAPABILITY_FILE = "capability.tlon"
REVIEWS_FILE = "REVIEW.md"


@dataclass(frozen=True)
class CapabilityInfo:
    """Metadata of one capability word.

    Mirrors the prototype channel (docs/design/capabilities.md):
    ``trusted`` defaults to True for user-installed capabilities
    (handwritten implies trusted); only the staging area writes
    ``:trusted false``. ``deprecated`` is the retirement marker
    (vocabulary only grows; a deprecated word is physically deleted only
    once the whole repo references it nowhere).

    ``description`` / ``pure`` are **derived** from the artifact
    (``capability.tlon``) — fail-soft like the prototype parameter
    reader: an unparseable artifact yields ``description=""`` and
    ``pure=None``, never a failed listing.
    """

    name: str
    description: str
    pure: bool | None
    trusted: bool = True
    author: str | None = None
    proposal_note: str | None = None
    deprecated: bool = False


def _is_capability_dir(cap_dir: Path) -> bool:
    """A directory qualifies when it carries the artifact file."""
    return (cap_dir / CAPABILITY_FILE).exists()


def _validate_name(name: str) -> None:
    """Capability directory names share the word-name charset.

    Kept permissive at the path layer (directory operations): alnum plus
    ``-`` / ``_``, non-empty. The stricter word-name rule (letter first)
    is enforced by the artifact schema in ``spec.py``.
    """
    if not name or not name.replace("-", "").replace("_", "").isalnum():
        raise CapabilityError(
            f"invalid capability name {name!r}: only letters, digits, '-' and '_' allowed"
        )


def _read_artifact(cap_dir: Path) -> tuple[str, bool | None]:
    """The artifact's ``(description, pure)`` — fail-soft for listings."""
    capability_file = cap_dir / CAPABILITY_FILE
    if not capability_file.exists():
        return "", None
    try:
        spec = CapabilitySpec.from_edn(capability_file.read_text(encoding="utf-8"))
        return spec.description, spec.pure
    except Exception as e:
        logger.warning("capability_artifact_unreadable", path=str(capability_file), error=str(e))
        return "", None


def _read_metadata(cap_dir: Any) -> CapabilityInfo:
    """Read ``capability.edn`` metadata, falling back to the directory name."""
    name = cap_dir.name
    description, pure = _read_artifact(cap_dir)
    metadata_file = cap_dir / METADATA_FILE
    if not metadata_file.exists():
        return CapabilityInfo(name=name, description=description, pure=pure)
    try:
        data = loads(metadata_file.read_text(encoding="utf-8"))
        author = data.get("author")
        note = data.get("proposal-note")
        return CapabilityInfo(
            name=str(data.get("name") or name),
            description=description,
            pure=pure,
            trusted=bool(data.get("trusted", True)),
            author=str(author) if author else None,
            proposal_note=str(note) if note else None,
            deprecated=bool(data.get("deprecated", False)),
        )
    except Exception as e:
        logger.warning("capability_metadata_invalid", path=str(metadata_file), error=str(e))
        return CapabilityInfo(name=name, description=description, pure=pure)
