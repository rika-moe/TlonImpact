"""Trusted-library management: human-authored create / update / delete."""

import shutil
from typing import Any

from tlon.agents.prototypes.info import (
    BEHAVIOR_FILE,
    LESSONS_FILE,
    METADATA_FILE,
    PrototypeInfo,
    _is_prototype_dir,
    _read_metadata,
    _resolve_parent_generation,
    _validate_display_name,
    _validate_name,
)
from tlon.agents.prototypes.staging import _validate_behavior
from tlon.base.identifiers import new_prototype_id
from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths
from tlon.tools.prototypes.ports import PrototypeError
from tlosure.edn import Keyword, dumps, loads

logger = get_logger(__name__)


def write_trusted_prototype(
    behavior_edn: str,
    *,
    display_name: str,
    description: str = "",
    parent: str | None = None,
    proposal_note: str | None = None,
    lessons: str | None = None,
    tlon_paths: TlonPaths,
) -> PrototypeInfo:
    """Write a human-authored prototype directly into the trusted library.

    Handwritten implies trusted (the same rule the metadata reader encodes):
    the metadata is written with ``:trusted true`` and no ``:author``.
    Validation happens **before** anything touches disk — the same
    chain as the staging gate. Lineage fields (see
    docs/design/prototypes.md) work exactly as in the staging write path.

    Args:
        behavior_edn: raw EDN text of the unified behavior spec
        display_name: required non-empty behavior label; does not assign a profession
        description: human-facing description
        parent: name of the prototype this one derives from (must exist in
            either area; None = a new root)
        proposal_note: free-text description of this generation's change
        lessons: lessons text (see docs/design/prototypes.md), written as
            ``LESSONS.md`` (None = the prototype carries no lessons file)
        tlon_paths: path resolver for the target layout

    Returns:
        Metadata of the created prototype (``trusted=True``).

    Raises:
        PrototypeError: empty behavior label, generated ID conflict, unknown parent, or spec
            failed validation (nothing is written in any case)
    """
    p = tlon_paths
    _validate_display_name(display_name)
    generation = _resolve_parent_generation(parent, p) if parent is not None else 1
    spec_text = _validate_behavior(behavior_edn)

    name = new_prototype_id()
    if (p.prototypes_dir() / name).exists() or (p.prototypes_staging_dir() / name).exists():
        raise PrototypeError(f"prototype '{name}' already exists")
    proto_dir = p.prototypes_dir() / name
    proto_dir.mkdir(parents=True)
    (proto_dir / BEHAVIOR_FILE).write_text(spec_text, encoding="utf-8")
    if lessons:
        (proto_dir / LESSONS_FILE).write_text(lessons, encoding="utf-8")

    metadata: dict[Any, Any] = {
        Keyword("name"): Keyword(name),
        Keyword("display-name"): display_name,
        Keyword("description"): description,
        Keyword("trusted"): True,
        Keyword("generation"): generation,
    }
    if parent is not None:
        metadata[Keyword("parent")] = Keyword(parent)
    if proposal_note:
        metadata[Keyword("proposal-note")] = proposal_note
    (proto_dir / METADATA_FILE).write_text(dumps(metadata) + "\n", encoding="utf-8")

    logger.info("trusted_prototype_written", name=name)
    return _read_metadata(proto_dir)


def update_prototype(
    name: str,
    *,
    display_name: str | None = None,
    description: str | None = None,
    behavior_edn: str | None = None,
    parent: str | None = None,
    proposal_note: str | None = None,
    lessons: str | None = None,
    staging: bool = False,
    tlon_paths: TlonPaths,
) -> PrototypeInfo:
    """Update a prototype's metadata and/or rewrite its behavior file.

    A behavior change runs the full validation chain first — a broken spec
    raises and leaves the disk untouched. The metadata rewrite preserves
    the existing ``:trusted``/``:author`` fields (only the explicitly
    provided fields change). Lineage (see docs/design/prototypes.md):
    providing ``parent`` re-links the prototype and re-derives its
    ``:generation`` server-side; ``proposal_note`` records what a
    generation changes.

    Args:
        name: prototype directory name
        display_name: new display name (None = keep)
        description: new description (None = keep)
        behavior_edn: new raw EDN behavior spec (None = keep)
        parent: new lineage parent (None = keep; must exist in either area)
        proposal_note: new change note (None = keep)
        lessons: new lessons text, rewritten as ``LESSONS.md``
            (None = keep)
        staging: update in the staging area instead of the trusted library
            (pre-approval editing of an LLM-authored prototype)
        tlon_paths: path resolver for the target layout

    Returns:
        Metadata of the updated prototype.

    Raises:
        PrototypeError: invalid name, prototype not found, unknown parent,
            nothing to update, or the new behavior failed validation
            (nothing is written in the last cases)
    """
    p = tlon_paths
    _validate_name(name)
    root = p.prototypes_staging_dir() if staging else p.prototypes_dir()
    proto_dir = root / name
    if not _is_prototype_dir(proto_dir):
        where = "staging area" if staging else "trusted library"
        raise PrototypeError(f"no prototype named '{name}' in the {where}")
    if (
        display_name is None
        and description is None
        and behavior_edn is None
        and parent is None
        and proposal_note is None
        and lessons is None
    ):
        raise PrototypeError(
            "nothing to update: provide display_name, description, behavior_edn, "
            "parent, proposal_note or lessons"
        )

    if display_name is not None:
        _validate_display_name(display_name)

    # Resolve everything that can fail before anything touches disk.
    parent_generation: int | None = None
    if parent is not None:
        parent_generation = _resolve_parent_generation(parent, p)
    spec_text = _validate_behavior(behavior_edn) if behavior_edn is not None else None

    if spec_text is not None:
        (proto_dir / BEHAVIOR_FILE).write_text(spec_text, encoding="utf-8")
    if lessons is not None:
        (proto_dir / LESSONS_FILE).write_text(lessons, encoding="utf-8")

    if (
        display_name is not None
        or description is not None
        or parent is not None
        or proposal_note is not None
    ):
        metadata_file = proto_dir / METADATA_FILE
        data: dict[Any, Any] = {}
        if metadata_file.exists():
            parsed = loads(metadata_file.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                data = dict(parsed)
        if display_name is not None:
            data[Keyword("display-name")] = display_name
        if description is not None:
            data[Keyword("description")] = description
        if parent is not None:
            data[Keyword("parent")] = Keyword(parent)
            data[Keyword("generation")] = parent_generation
        if proposal_note is not None:
            data[Keyword("proposal-note")] = proposal_note
        data.setdefault(Keyword("name"), Keyword(name))
        data.setdefault(Keyword("display-name"), name)
        data.setdefault(Keyword("description"), "")
        data.setdefault(Keyword("trusted"), not staging)
        metadata_file.write_text(dumps(data) + "\n", encoding="utf-8")

    logger.info(
        "prototype_updated",
        name=name,
        staging=staging,
        behavior_rewritten=spec_text is not None,
    )
    return _read_metadata(proto_dir)


def delete_prototype(name: str, tlon_paths: TlonPaths) -> None:
    """Delete a prototype from the trusted library.

    Deleting a staged prototype is the existing ``reject_prototype``; this
    is the trusted-library counterpart. Path-injection guard: the name
    must pass the charset check and resolve strictly inside the library
    root.

    Raises:
        PrototypeError: invalid name or no trusted prototype under that name
    """
    p = tlon_paths
    _validate_name(name)
    library_root = p.prototypes_dir().resolve()
    target = (library_root / name).resolve()
    if target.parent != library_root:
        raise PrototypeError(f"prototype name '{name}' resolves outside the trusted library")
    if not _is_prototype_dir(target):
        raise PrototypeError(f"no prototype named '{name}' in the trusted library")
    shutil.rmtree(target)
    logger.info("prototype_deleted", name=name)
