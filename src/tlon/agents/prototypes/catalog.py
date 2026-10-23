"""Trusted-library catalog: seeding built-in prototypes, listing, and
metadata / full-detail reads.
"""

from collections.abc import Iterator
from importlib import resources
from importlib.resources.abc import Traversable
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
from tlon.evolution.ports import PrototypeError
from tlon.evolution.publication import publish_directory

logger = get_logger(__name__)


def _resource_files(root: Traversable, prefix: str = "") -> Iterator[tuple[str, Traversable]]:
    """Walk packaged source directories without relying on filesystem paths."""
    for resource in root.iterdir():
        if resource.name.startswith(("_", ".")):
            continue
        relative = prefix + resource.name
        if resource.is_dir():
            yield from _resource_files(resource, relative + "/")
        elif resource.is_file():
            yield relative, resource


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
        # Existing packages belong to the user, including older formats.
        # Never inject a new module set into an existing behavior package.
        if target_dir.exists():
            continue
        content = {relative: resource.read_bytes() for relative, resource in _resource_files(entry)}
        publish_directory(target_dir, content)
        copied += len(content)
    logger.info("prototypes_seeded", copied=copied, dir=str(target_root))
    return copied


def list_prototypes(tlon_paths: TlonPaths) -> list[PrototypeInfo]:
    """List prototypes from ``~/.tlon/prototypes/`` (not the package).

    A directory qualifies when it contains a ``behavior.tlon``; metadata is
    read from ``prototype.tlon`` when present, with a name-only fallback.
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


def get_prototype_info(name: str, tlon_paths: TlonPaths) -> PrototypeInfo | None:
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
        ``LESSONS.md`` text (see docs/design/prototypes.md), None when the
        prototype carries no lessons file; ``reviews`` is the ``REVIEW.md``
        text (heterogeneous review records, see docs/design/prototypes.md),
        None when no review has landed.

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


def get_prototype_modules(
    name: str, *, staging: bool = False, tlon_paths: TlonPaths
) -> dict[str, str]:
    """Read all author sources through the same validated prototype lookup."""
    from tlon.agents.program_package import read_module_sources

    _validate_name(name)
    get_prototype_detail(name, staging=staging, tlon_paths=tlon_paths)
    root = tlon_paths.prototypes_staging_dir() if staging else tlon_paths.prototypes_dir()
    try:
        return read_module_sources(root / name / "modules")
    except ValueError as error:
        raise PrototypeError(str(error)) from error
