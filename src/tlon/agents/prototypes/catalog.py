"""Trusted-library catalog: seeding built-in prototypes, listing, and
metadata / full-detail reads.
"""

from importlib import resources
from pathlib import Path

from tlon.agents.prototypes.info import (
    BEHAVIOR_FILE,
    LESSONS_FILE,
    PROTOTYPE_PACKAGE,
    REVIEWS_FILE,
    PrototypeInfo,
    _is_prototype_dir,
    _read_metadata,
    _validate_name,
)
from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths
from tlon.tools.prototypes.ports import PrototypeError

logger = get_logger(__name__)


def seed_prototypes(tlon_paths: TlonPaths) -> int:
    """Copy built-in prototypes to ``~/.tlon/prototypes/`` (idempotent).

    Existing files are never overwritten: user edits and user-added
    prototypes survive re-seeding.

    Returns:
        Number of files copied (0 when everything already exists).
    """
    p = tlon_paths
    target_root = p.prototypes_dir()
    copied = 0
    for entry in resources.files(PROTOTYPE_PACKAGE).iterdir():
        # __pycache__ appears when running from a source tree
        if not entry.is_dir() or entry.name.startswith(("_", ".")):
            continue
        target_dir = target_root / entry.name
        for resource in entry.iterdir():
            if not resource.is_file() or resource.name.startswith(("_", ".")):
                continue
            target = target_dir / resource.name
            if target.exists():
                continue
            target_dir.mkdir(parents=True, exist_ok=True)
            target.write_bytes(resource.read_bytes())
            copied += 1
    logger.info("prototypes_seeded", copied=copied, dir=str(target_root))
    return copied


def list_prototypes(tlon_paths: TlonPaths) -> list[PrototypeInfo]:
    """List prototypes from ``~/.tlon/prototypes/`` (not the package).

    A directory qualifies when it contains a ``behavior.tlon``; metadata is
    read from ``prototype.edn`` when present, with a name-only fallback.
    """
    return _list_from((tlon_paths).prototypes_dir())


def _list_from(root: Path) -> list[PrototypeInfo]:
    """List qualifying prototype directories under ``root``."""
    if not root.exists():
        return []
    result = []
    for proto_dir in sorted(root.iterdir()):
        if not proto_dir.is_dir() or not _is_prototype_dir(proto_dir):
            continue
        result.append(_read_metadata(proto_dir))
    return result


def get_prototype_info(
    name: str, tlon_paths: TlonPaths
) -> PrototypeInfo | None:
    """Metadata of a trusted-library prototype, or None when absent."""
    proto_dir = (tlon_paths).prototypes_dir() / name
    if not _is_prototype_dir(proto_dir):
        return None
    return _read_metadata(proto_dir)


def get_prototype_detail(
    name: str, staging: bool = False, *, tlon_paths: TlonPaths
) -> tuple[PrototypeInfo, str, str | None, str | None]:
    """Metadata plus the full ``behavior.tlon`` text of one prototype.

    Args:
        name: prototype directory name
        staging: read from the staging area instead of the trusted library
        tlon_paths: path resolver for the target layout

    Returns:
        ``(metadata, behavior_text, lessons, reviews)`` — ``lessons`` is the
        ``LESSONS.md`` text (issue ), None when the prototype carries
        no lessons file; ``reviews`` is the ``REVIEW.md`` text (issue ,
        heterogeneous review records), None when no review has landed.

    Raises:
        PrototypeError: invalid name or no prototype under that name
    """
    p = tlon_paths
    _validate_name(name)
    root = p.prototypes_staging_dir() if staging else p.prototypes_dir()
    proto_dir = root / name
    if not _is_prototype_dir(proto_dir):
        where = "staging area" if staging else "trusted library"
        raise PrototypeError(f"no prototype named '{name}' in the {where}")
    info = _read_metadata(proto_dir)
    behavior = (proto_dir / BEHAVIOR_FILE).read_text(encoding="utf-8")
    lessons_file = proto_dir / LESSONS_FILE
    lessons = lessons_file.read_text(encoding="utf-8") if lessons_file.exists() else None
    reviews_file = proto_dir / REVIEWS_FILE
    reviews = reviews_file.read_text(encoding="utf-8") if reviews_file.exists() else None
    return info, behavior, lessons, reviews
