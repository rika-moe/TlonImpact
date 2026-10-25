"""Staging area (stage 4a trust gate): LLM-authored prototypes and the
human approval flow.
"""

import shutil
from typing import Any

from tlon.agents.behavior_template import BehaviorTemplateError, analyze, render_module_templates
from tlon.agents.program_package import (
    program_digest,
    read_module_sources,
    validate_program_sources,
)
from tlon.agents.prototypes.catalog import _list_from
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
from tlon.agents.prototypes.reviews import staging_lock
from tlon.base.identifiers import new_prototype_id
from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths
from tlon.evolution.ports import PrototypeError
from tlon.evolution.publication import publish_directory
from tlosure.edn import Keyword, dumps, loads

logger = get_logger(__name__)


# ==================== Staging area (stage 4a trust gate) ====================


def _validate_behavior(
    behavior_edn: str, *, tlon_paths: TlonPaths, modules: dict[str, str] | None = None
) -> str:
    """Run the write-side validation chain; return the normalized spec text.

    The gate runs the same chain a runtime load would — template analysis,
    EDN parse, then the single validator with host words.
    No template or operation is executed.

    Args:
        behavior_edn: raw EDN text of the unified behavior spec

    Returns:
        The spec text normalized to end with a newline.

    Raises:
        PrototypeError: the spec failed template / EDN / schema validation
    """
    spec_text = behavior_edn if behavior_edn.endswith("\n") else behavior_edn + "\n"
    try:
        template = analyze(spec_text, modules)
        validate_program_sources(
            template.standin_text,
            render_module_templates(spec_text, modules or {}, {}, standins=True),
        )
    except (BehaviorTemplateError, ValueError) as e:
        raise PrototypeError(f"behavior program validation failed: {e}") from e
    return spec_text


def write_staging_prototype(
    behavior_edn: str,
    *,
    display_name: str,
    description: str = "",
    author_id: str = "",
    parent: str | None = None,
    proposal_note: str | None = None,
    lessons: str | None = None,
    modules: dict[str, str] | None = None,
    tlon_paths: TlonPaths,
) -> PrototypeInfo:
    """Write an LLM-authored prototype to the staging area (untrusted).

    Validation happens **before** anything touches disk.

    The output is a complete module program: ``behavior.tlon`` and module sources — prototypes carry no persona (unified
    member model, ADR 0006); a member's persona files are seeded at
    creation and member-owned afterwards (self-editable via
    ``persona-update``, ADR 0024), never part of the behavior genome.

    The behavior text may be a write-side template: when it declares
    ``:parameters`` and references ``${var}`` placeholders, the declaration
    is validated and the text is checked structurally (placeholders
    substituted to type stand-ins). A concrete (non-template) text must
    parse as EDN and pass the unified ``MemberSpec`` schema.

    The metadata is written with ``:trusted false`` and ``:author`` recording
    the authoring agent, so ``member-spawn`` refuses it until a human approves
    it (ops/WebUI). Lineage (see docs/design/prototypes.md): ``parent``
    links the proposal to the prototype it was derived from (trusted or
    staging) and the generation is derived server-side as the parent's + 1;
    ``proposal_note`` records what this generation changes.

    Args:
        behavior_edn: raw EDN text of the unified behavior spec
        display_name: required non-empty behavior label; does not assign a profession
        description: human-facing description
        author_id: id of the agent that authored the spec (audit)
        parent: name of the prototype this one derives from (must exist in
            either area; None = a new root)
        proposal_note: free-text description of this generation's change
        lessons: lessons text (see docs/design/prototypes.md), written as
            ``LESSONS.md`` (None = the prototype carries no lessons file)
        tlon_paths: path resolver for the target layout

    Returns:
        Metadata of the staged prototype (``trusted=False``).

    Raises:
        PrototypeError: empty behavior label, generated ID conflict, unknown parent, or spec
            failed template / EDN / schema validation (nothing is written in
            any case)
    """
    p = tlon_paths
    _validate_display_name(display_name)
    # Validate first: a broken spec must never reach disk.
    generation = _resolve_parent_generation(parent, p) if parent is not None else 1
    spec_text = _validate_behavior(behavior_edn, tlon_paths=p, modules=modules)

    name = new_prototype_id()
    if (p.prototypes_dir() / name).exists() or (p.prototypes_staging_dir() / name).exists():
        raise PrototypeError(f"prototype '{name}' already exists")
    staging_dir = p.prototypes_staging_dir() / name

    metadata: dict[Any, Any] = {
        Keyword("name"): Keyword(name),
        Keyword("display-name"): display_name,
        Keyword("description"): description,
        Keyword("trusted"): False,
        Keyword("author"): author_id,
        Keyword("generation"): generation,
    }
    if parent is not None:
        metadata[Keyword("parent")] = Keyword(parent)
    if proposal_note:
        metadata[Keyword("proposal-note")] = proposal_note
    metadata[Keyword("program-digest")] = program_digest(spec_text, modules or {})
    files = {BEHAVIOR_FILE: spec_text, METADATA_FILE: dumps(metadata) + "\n"}
    if lessons:
        files[LESSONS_FILE] = lessons
    files.update({f"modules/{name}": source for name, source in (modules or {}).items()})
    publish_directory(staging_dir, files)

    logger.info(
        "staging_prototype_written",
        name=name,
        author_id=author_id,
        parent=parent,
        generation=generation,
    )
    return _read_metadata(staging_dir)


def list_staging_prototypes(tlon_paths: TlonPaths) -> list[PrototypeInfo]:
    """List prototypes awaiting approval in ``~/.tlon/prototypes-staging/``."""
    return _list_from((tlon_paths).prototypes_staging_dir())


def approve_prototype(name: str, tlon_paths: TlonPaths) -> PrototypeInfo:
    """Approve a package without racing concurrent review submissions."""
    with staging_lock(tlon_paths):
        return _approve_prototype_locked(name, tlon_paths)


def _approve_prototype_locked(name: str, tlon_paths: TlonPaths) -> PrototypeInfo:
    """Promote a staged prototype to the trusted library (human approval).

    Moves the directory to ``~/.tlon/prototypes/`` and rewrites its
    metadata with ``:trusted true``; the ``:author`` record is kept.

    Raises:
        PrototypeError: invalid name, nothing staged under that name, or the
            trusted library already has a prototype with that name
    """
    p = tlon_paths
    _validate_name(name)
    staging_dir = p.prototypes_staging_dir() / name
    if not _is_prototype_dir(staging_dir):
        raise PrototypeError(f"no staged prototype named '{name}'")
    target_dir = p.prototypes_dir() / name
    if target_dir.exists():
        raise PrototypeError(f"prototype '{name}' already exists in the trusted library")

    raw_text = (staging_dir / BEHAVIOR_FILE).read_text(encoding="utf-8")
    try:
        modules = read_module_sources(staging_dir / "modules")
    except ValueError as e:
        raise PrototypeError(str(e)) from e
    _validate_behavior(raw_text, tlon_paths=p, modules=modules)
    metadata_file = staging_dir / METADATA_FILE
    data: dict[Any, Any] = {}
    if metadata_file.is_symlink():
        raise PrototypeError("prototype metadata must not be a symlink")
    if metadata_file.exists():
        parsed = loads(metadata_file.read_text(encoding="utf-8"))
        if isinstance(parsed, dict):
            data = dict(parsed)
    data[Keyword("trusted")] = True
    data[Keyword("program-digest")] = program_digest(raw_text, modules)
    data.setdefault(Keyword("name"), Keyword(name))
    data.setdefault(Keyword("display-name"), name)
    data.setdefault(Keyword("description"), "")
    files: dict[str, str | bytes] = {BEHAVIOR_FILE: raw_text, METADATA_FILE: dumps(data) + "\n"}
    files.update({f"modules/{path}": source for path, source in modules.items()})
    # Keep review and arena evidence together with the approved package.
    for source in sorted(staging_dir.rglob("*")):
        if source.is_symlink():
            raise PrototypeError(f"symlink is not allowed: {source}")
        if source.is_file():
            relative = source.relative_to(staging_dir).as_posix()
            if relative not in files:
                files[relative] = source.read_bytes()
    publish_directory(target_dir, files)
    shutil.rmtree(staging_dir)

    logger.info("prototype_approved", name=name, author=str(data.get("author") or ""))
    return _read_metadata(target_dir)


def reject_prototype(name: str, tlon_paths: TlonPaths) -> None:
    """Reject a package without racing concurrent review submissions."""
    with staging_lock(tlon_paths):
        _reject_prototype_locked(name, tlon_paths)


def _reject_prototype_locked(name: str, tlon_paths: TlonPaths) -> None:
    """Delete a staged prototype (human rejection).

    Path-injection guard: the name must pass the charset check and resolve
    strictly inside the staging root.

    Raises:
        PrototypeError: invalid name or nothing staged under that name
    """
    p = tlon_paths
    _validate_name(name)
    staging_root = p.prototypes_staging_dir().resolve()
    if (staging_root / name).is_symlink():
        raise PrototypeError("staged prototype must not be a symlink")
    target = (staging_root / name).resolve()
    if target.parent != staging_root:
        raise PrototypeError(f"prototype name '{name}' resolves outside the staging area")
    if not target.is_dir():
        raise PrototypeError(f"no staged prototype named '{name}'")
    shutil.rmtree(target)
    logger.info("prototype_rejected", name=name)
