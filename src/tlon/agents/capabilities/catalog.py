"""Review catalog joins file metadata with authoritative compilation results."""

from dataclasses import replace
from pathlib import Path

from tlon.agents.capabilities.bridge import load_capability_catalog, reserved_capability_names
from tlon.agents.capabilities.info import (
    CAPABILITY_FILE,
    REVIEWS_FILE,
    CapabilityInfo,
    _is_capability_dir,
    _read_metadata,
    _validate_name,
)
from tlon.agents.capabilities.spec import (
    CapabilityArtifact,
    CapabilityError,
    ExampleResult,
    evaluate_capability_examples,
)
from tlon.base.paths import TlonPaths
from tlon.operations.composite import CompositeCatalog, compile_composites
from tlon.operations.definitions import host_operation_definitions


def _candidate_catalog(artifact: CapabilityArtifact, paths: TlonPaths) -> CompositeCatalog:
    if artifact.name in reserved_capability_names():
        raise CapabilityError(f"capability name collides with reserved vocabulary: {artifact.name}")
    trusted = load_capability_catalog(paths)
    specs = {name: definition.spec for name, definition in trusted.definitions.items()}
    specs[artifact.name] = artifact.spec
    return compile_composites(specs.values(), host_operation_definitions(), strict=False)


def _list_from(root: Path) -> list[CapabilityInfo]:
    if not root.exists():
        return []
    return [
        _read_metadata(directory)
        for directory in sorted(root.iterdir())
        if directory.is_dir() and _is_capability_dir(directory)
    ]


def capability_info(name: str, paths: TlonPaths, *, staging: bool = False) -> CapabilityInfo:
    root = paths.capabilities_staging_dir() if staging else paths.capabilities_dir()
    directory = root / name
    info = _read_metadata(directory)
    try:
        artifact = CapabilityArtifact.from_edn(
            (directory / CAPABILITY_FILE).read_text(encoding="utf-8")
        )
        catalog = _candidate_catalog(artifact, paths) if staging else load_capability_catalog(paths)
        definition = catalog.definitions.get(name)
        if definition is None:
            return replace(info, error=catalog.errors.get(name, "capability is unavailable"))
        return replace(
            info,
            effects=tuple(sorted(definition.effects)),
            dependencies=definition.dependency_ids,
            definition_id=definition.definition_id,
            error=None,
        )
    except (CapabilityError, OSError) as error:
        return replace(info, error=str(error))


def list_trusted_capabilities(tlon_paths: TlonPaths) -> list[CapabilityInfo]:
    return [
        capability_info(info.name, tlon_paths) for info in _list_from(tlon_paths.capabilities_dir())
    ]


def get_capability_info(name: str, tlon_paths: TlonPaths) -> CapabilityInfo | None:
    _validate_name(name)
    if not _is_capability_dir(tlon_paths.capabilities_dir() / name):
        return None
    return capability_info(name, tlon_paths)


def get_capability_detail(
    name: str, staging: bool = False, *, tlon_paths: TlonPaths
) -> tuple[CapabilityInfo, str, str | None]:
    _validate_name(name)
    root = tlon_paths.capabilities_staging_dir() if staging else tlon_paths.capabilities_dir()
    directory = root / name
    if not _is_capability_dir(directory):
        raise CapabilityError(f"no capability named '{name}'")
    reviews = directory / REVIEWS_FILE
    return (
        capability_info(name, tlon_paths, staging=staging),
        (directory / CAPABILITY_FILE).read_text(encoding="utf-8"),
        reviews.read_text(encoding="utf-8") if reviews.exists() else None,
    )


def run_capability_examples(
    name: str, staging: bool = False, *, tlon_paths: TlonPaths
) -> list[ExampleResult]:
    try:
        _info, text, _reviews = get_capability_detail(name, staging=staging, tlon_paths=tlon_paths)
        artifact = CapabilityArtifact.from_edn(text)
        catalog = _candidate_catalog(artifact, tlon_paths)
        if artifact.name not in catalog.definitions:
            return [
                ExampleResult(
                    item.input,
                    item.expect,
                    False,
                    error=catalog.errors.get(name, "definition unavailable"),
                )
                for item in artifact.examples
            ]
        return evaluate_capability_examples(artifact, catalog)
    except CapabilityError:
        return []
