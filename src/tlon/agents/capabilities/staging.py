"""Staging area (trust gate): LLM-authored capability artifacts and the
human approval flow — structurally aligned with the prototype channel
(``agents/prototypes/staging.py``, docs/design/capabilities.md).
"""

import shutil
from typing import Any

from tlon.agents.capabilities.catalog import _list_from
from tlon.agents.capabilities.info import (
    CAPABILITY_FILE,
    METADATA_FILE,
    REVIEWS_FILE,
    CapabilityInfo,
    _is_capability_dir,
    _read_metadata,
    _validate_name,
)
from tlon.agents.capabilities.spec import (
    CapabilityError,
    CapabilitySpec,
    validate_capability,
)
from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths
from tlosure.edn import Keyword, dumps, loads

logger = get_logger(__name__)


def _trusted_word_names(p: TlonPaths) -> set[str]:
    """The word names of the trusted library (directory names — the
    staging channel writes directories named after the artifact's
    ``:name``, and the trusted library follows the same convention)."""
    root = p.capabilities_dir()
    if not root.exists():
        return set()
    return {d.name for d in root.iterdir() if d.is_dir() and _is_capability_dir(d)}


def write_staging_capability(
    capability_edn: str,
    author_id: str = "",
    proposal_note: str | None = None,
    *,
    tlon_paths: TlonPaths,
) -> CapabilityInfo:
    """Write an LLM-authored capability artifact to the staging area (untrusted).

    Validation happens **before** anything touches disk: artifact parse
    and structural checks, then the full load gate (namespace rejection
    against the trusted library, purity admission, impl static
    validation). The capability's identity is the artifact's ``:name`` —
    the staging directory is named after it (one artifact, one word).

    The metadata is written with ``:trusted false`` and ``:author``
    recording the authoring agent, so the loader must refuse the word
    until a human approves it.

    Args:
        capability_edn: raw EDN text of the capability artifact
        author_id: id of the agent that authored the artifact (audit)
        proposal_note: free-text description of what this word adds
        tlon_paths: path resolver for the target layout

    Returns:
        Metadata of the staged capability (``trusted=False``).

    Raises:
        CapabilityError: invalid artifact, name conflict with the trusted
            library or the staging area, or validation failure (nothing
            is written in any case)
    """
    p = tlon_paths

    # Validate first: a broken artifact must never reach disk.
    spec = CapabilitySpec.from_edn(capability_edn)
    name = spec.name
    _validate_name(name)
    validate_capability(spec, taken_names=_trusted_word_names(p))

    if (p.capabilities_dir() / name / CAPABILITY_FILE).exists():
        raise CapabilityError(f"capability '{name}' already exists in the trusted library")
    staging_dir = p.capabilities_staging_dir() / name
    if staging_dir.exists():
        raise CapabilityError(f"capability '{name}' already exists in the staging area")

    staging_dir.mkdir(parents=True)

    capability_text = capability_edn if capability_edn.endswith("\n") else capability_edn + "\n"
    (staging_dir / CAPABILITY_FILE).write_text(capability_text, encoding="utf-8")

    metadata: dict[Any, Any] = {
        Keyword("name"): Keyword(name),
        Keyword("trusted"): False,
        Keyword("author"): author_id,
    }
    if proposal_note:
        metadata[Keyword("proposal-note")] = proposal_note
    (staging_dir / METADATA_FILE).write_text(dumps(metadata) + "\n", encoding="utf-8")

    logger.info(
        "staging_capability_written",
        name=name,
        author_id=author_id,
        pure=spec.pure,
    )
    return _read_metadata(staging_dir)


def list_staging_capabilities(tlon_paths: TlonPaths) -> list[CapabilityInfo]:
    """List capabilities awaiting approval in ``~/.tlon/capabilities-staging/``."""
    return _list_from(tlon_paths.capabilities_staging_dir())


def approve_capability(name: str, tlon_paths: TlonPaths) -> CapabilityInfo:
    """Promote a staged capability to the trusted library (human approval).

    Moves the directory to ``~/.tlon/capabilities/`` and rewrites its
    metadata with ``:trusted true``; the ``:author`` record is kept.
    The whole-directory move carries any ``REVIEW.md`` along, so the
    review trail survives promotion.

    Raises:
        CapabilityError: invalid name, nothing staged under that name, or the
            trusted library already has a capability with that name
    """
    p = tlon_paths
    _validate_name(name)
    staging_dir = p.capabilities_staging_dir() / name
    if not _is_capability_dir(staging_dir):
        raise CapabilityError(f"no staged capability named '{name}'")
    target_dir = p.capabilities_dir() / name
    if target_dir.exists():
        raise CapabilityError(f"capability '{name}' already exists in the trusted library")

    target_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(staging_dir), str(target_dir))

    metadata_file = target_dir / METADATA_FILE
    data: dict[Any, Any] = {}
    if metadata_file.exists():
        parsed = loads(metadata_file.read_text(encoding="utf-8"))
        if isinstance(parsed, dict):
            data = dict(parsed)
    data[Keyword("trusted")] = True
    data.setdefault(Keyword("name"), Keyword(name))
    metadata_file.write_text(dumps(data) + "\n", encoding="utf-8")

    logger.info("capability_approved", name=name, author=str(data.get("author") or ""))
    return _read_metadata(target_dir)


def reject_capability(name: str, tlon_paths: TlonPaths) -> None:
    """Delete a staged capability (human rejection).

    Path-injection guard: the name must pass the charset check and resolve
    strictly inside the staging root.

    Raises:
        CapabilityError: invalid name or nothing staged under that name
    """
    p = tlon_paths
    _validate_name(name)
    staging_root = p.capabilities_staging_dir().resolve()
    target = (staging_root / name).resolve()
    if target.parent != staging_root:
        raise CapabilityError(f"capability name '{name}' resolves outside the staging area")
    if not target.is_dir():
        raise CapabilityError(f"no staged capability named '{name}'")
    shutil.rmtree(target)
    logger.info("capability_rejected", name=name)


def append_staging_review(name: str, entry_text: str, tlon_paths: TlonPaths) -> None:
    """Append one peer-review entry to a staged capability's ``REVIEW.md``.

    The review record is advisory (aligned with the prototype channel):
    it never gates approval — it rides along with the directory when
    ``approve_capability`` moves the capability into the trusted library,
    so the review trail survives promotion. Multiple reviews accumulate;
    nothing overwrites.

    Args:
        name: staged capability directory name
        entry_text: one formatted review entry (timestamp, reviewer id,
            reviewer model, verdict, rationale — assembled by the caller);
            normalized to end with a newline
        tlon_paths: path resolver for the target layout

    Raises:
        CapabilityError: invalid name or nothing staged under that name
    """
    p = tlon_paths
    _validate_name(name)
    staging_dir = p.capabilities_staging_dir() / name
    if not _is_capability_dir(staging_dir):
        raise CapabilityError(f"no staged capability named '{name}'")
    entry = entry_text if entry_text.endswith("\n") else entry_text + "\n"
    review_file = staging_dir / REVIEWS_FILE
    if review_file.exists():
        with review_file.open("a", encoding="utf-8") as f:
            f.write("\n" + entry)
    else:
        review_file.write_text("# 评审记录\n\n" + entry, encoding="utf-8")
    logger.info("staging_capability_review_appended", name=name)
