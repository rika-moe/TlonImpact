"""Human approval publishes a frozen composite dependency contract."""

import shutil
from typing import Any

from tlon.agents.capabilities.catalog import _candidate_catalog, _list_from, capability_info
from tlon.agents.capabilities.info import (
    CAPABILITY_FILE,
    METADATA_FILE,
    REVIEWS_FILE,
    CapabilityInfo,
    _is_capability_dir,
    _validate_name,
)
from tlon.agents.capabilities.spec import CapabilityArtifact, CapabilityError, validate_examples
from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths
from tlon.evolution.publication import publish_directory
from tlosure.edn import Keyword, dumps, loads

logger = get_logger(__name__)


def write_staging_capability(
    capability_edn: str,
    author_id: str = "",
    proposal_note: str | None = None,
    *,
    tlon_paths: TlonPaths,
) -> CapabilityInfo:
    artifact = CapabilityArtifact.from_edn(capability_edn)
    name = artifact.name
    _validate_name(name)
    if (tlon_paths.capabilities_dir() / name / CAPABILITY_FILE).exists():
        raise CapabilityError(f"capability '{name}' already exists in the trusted library")
    directory = tlon_paths.capabilities_staging_dir() / name
    if directory.exists():
        raise CapabilityError(f"capability '{name}' already exists in the staging area")
    catalog = _candidate_catalog(artifact, tlon_paths)
    if name not in catalog.definitions:
        raise CapabilityError(catalog.errors.get(name, "capability cannot be compiled"))
    validate_examples(artifact, catalog)
    metadata: dict[Any, Any] = {
        Keyword("name"): name,
        Keyword("trusted"): False,
        Keyword("author"): author_id,
    }
    if proposal_note:
        metadata[Keyword("proposal-note")] = proposal_note
    publish_directory(
        directory,
        {CAPABILITY_FILE: capability_edn.rstrip() + "\n", METADATA_FILE: dumps(metadata) + "\n"},
    )
    logger.info("staging_capability_written", name=name, author_id=author_id)
    return capability_info(name, tlon_paths, staging=True)


def list_staging_capabilities(tlon_paths: TlonPaths) -> list[CapabilityInfo]:
    return [
        capability_info(info.name, tlon_paths, staging=True)
        for info in _list_from(tlon_paths.capabilities_staging_dir())
    ]


def approve_capability(name: str, tlon_paths: TlonPaths) -> CapabilityInfo:
    """Approve staged content, or explicitly reapprove edited trusted content.

    Validation and fake examples complete before publication. Approval records
    bind both the definition and its transitive dependency identities.
    """
    _validate_name(name)
    staged = tlon_paths.capabilities_staging_dir() / name
    target = tlon_paths.capabilities_dir() / name
    is_staging = _is_capability_dir(staged)
    source = staged if is_staging else target
    if not _is_capability_dir(source):
        raise CapabilityError(f"no staged or trusted capability named '{name}'")
    if is_staging and target.exists():
        raise CapabilityError(f"capability '{name}' already exists in the trusted library")
    artifact = CapabilityArtifact.from_edn((source / CAPABILITY_FILE).read_text(encoding="utf-8"))
    if artifact.name != name:
        raise CapabilityError("artifact name must match its directory")
    catalog = _candidate_catalog(artifact, tlon_paths)
    if name not in catalog.definitions:
        raise CapabilityError(catalog.errors.get(name, "capability cannot be compiled"))
    validate_examples(artifact, catalog)
    definition = catalog.definitions[name]
    data: dict[Any, Any] = {}
    metadata_file = source / METADATA_FILE
    if metadata_file.exists():
        try:
            parsed = loads(metadata_file.read_text(encoding="utf-8"))
        except ValueError as error:
            raise CapabilityError(f"invalid capability metadata: {error}") from error
        if type(parsed) is not dict:
            raise CapabilityError("capability metadata must be a map")
        data.update(parsed)
    data[Keyword("name")] = name
    data[Keyword("trusted")] = True
    data[Keyword("definition-id")] = definition.definition_id
    data[Keyword("dependencies")] = definition.dependency_ids
    metadata = dumps(data) + "\n"
    if is_staging:
        files = {
            file.name: file.read_text(encoding="utf-8")
            for file in source.iterdir()
            if file.is_file()
        }
        files[METADATA_FILE] = metadata
        publish_directory(target, files)
        shutil.rmtree(source)
    else:
        temporary = metadata_file.with_suffix(".tmp")
        temporary.write_text(metadata, encoding="utf-8")
        temporary.replace(metadata_file)
    logger.info("capability_approved", name=name)
    return capability_info(name, tlon_paths)


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
