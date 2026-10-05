"""Trusted-library catalog: listing and metadata / full-detail reads.

There is no seeding (no built-in capability package ships yet — the
trusted library starts empty and grows through staging approvals and
human handwriting), so unlike the prototypes catalog this module carries
no ``seed_*`` function.
"""

from pathlib import Path

from tlon.agents.capabilities.info import (
    CAPABILITY_FILE,
    REVIEWS_FILE,
    CapabilityInfo,
    _is_capability_dir,
    _read_metadata,
    _validate_name,
)
from tlon.agents.capabilities.spec import (
    CapabilityError,
    CapabilitySpec,
    ExampleResult,
    evaluate_capability_examples,
)
from tlon.base.paths import TlonPaths


def list_trusted_capabilities(tlon_paths: TlonPaths) -> list[CapabilityInfo]:
    """List capabilities from ``~/.tlon/capabilities/`` (the trusted library).

    A directory qualifies when it contains a ``capability.tlon``;
    metadata is read from ``capability.edn`` when present, with a
    name-only fallback.
    """
    return _list_from(tlon_paths.capabilities_dir())


def _list_from(root: Path) -> list[CapabilityInfo]:
    """List qualifying capability directories under ``root``."""
    if not root.exists():
        return []
    result = []
    for cap_dir in sorted(root.iterdir()):
        if not cap_dir.is_dir() or not _is_capability_dir(cap_dir):
            continue
        result.append(_read_metadata(cap_dir))
    return result


def get_capability_info(name: str, tlon_paths: TlonPaths) -> CapabilityInfo | None:
    """Metadata of a trusted-library capability, or None when absent."""
    cap_dir = tlon_paths.capabilities_dir() / name
    if not _is_capability_dir(cap_dir):
        return None
    return _read_metadata(cap_dir)


def get_capability_detail(
    name: str, staging: bool = False, *, tlon_paths: TlonPaths
) -> tuple[CapabilityInfo, str, str | None]:
    """Metadata plus the full ``capability.tlon`` text of one capability.

    Returns:
        ``(metadata, capability_text, reviews)`` — ``reviews`` is the
        ``REVIEW.md`` text (heterogeneous review records, advisory),
        None when no review has landed.

    Raises:
        CapabilityError: invalid name or no capability under that name
    """
    p = tlon_paths
    _validate_name(name)
    root = p.capabilities_staging_dir() if staging else p.capabilities_dir()
    cap_dir = root / name
    if not _is_capability_dir(cap_dir):
        where = "staging area" if staging else "trusted library"
        raise CapabilityError(f"no capability named '{name}' in the {where}")
    info = _read_metadata(cap_dir)
    capability = (cap_dir / CAPABILITY_FILE).read_text(encoding="utf-8")
    reviews_file = cap_dir / REVIEWS_FILE
    reviews = reviews_file.read_text(encoding="utf-8") if reviews_file.exists() else None
    return info, capability, reviews


def run_capability_examples(
    name: str, staging: bool = False, *, tlon_paths: TlonPaths
) -> list[ExampleResult]:
    """Run one capability's ``:examples`` assertions (review material).

    Fail-soft: no capability under that name or an unparseable artifact
    yields an empty list — the detail endpoint never fails on example
    evaluation.
    """
    try:
        _info, artifact, _reviews = get_capability_detail(
            name, staging=staging, tlon_paths=tlon_paths
        )
        spec = CapabilitySpec.from_edn(artifact)
    except CapabilityError:
        return []
    return evaluate_capability_examples(spec)
