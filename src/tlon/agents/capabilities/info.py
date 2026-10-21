"""Capability review metadata; execution facts come from the composite compiler."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tlon.agents.capabilities.spec import CapabilityArtifact, CapabilityError
from tlosure.edn import loads

METADATA_FILE = "meta.tlon"
CAPABILITY_FILE = "capability.tlon"
REVIEWS_FILE = "REVIEW.md"


@dataclass(frozen=True)
class CapabilityInfo:
    name: str
    description: str = ""
    effects: tuple[str, ...] = ()
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] | bool = True
    dependencies: dict[str, str] = field(default_factory=dict)
    definition_id: str | None = None
    error: str | None = None
    trusted: bool = False
    author: str | None = None
    proposal_note: str | None = None
    deprecated: bool = False


def _is_capability_dir(cap_dir: Path) -> bool:
    return (cap_dir / CAPABILITY_FILE).is_file()


def _validate_name(name: str) -> None:
    if not name or not name.replace("-", "").replace("_", "").isalnum():
        raise CapabilityError(f"invalid capability name {name!r}")


def _read_metadata(cap_dir: Path) -> CapabilityInfo:
    """Read review facts without claiming an uncompiled artifact is executable."""
    data: dict[str, Any] = {}
    metadata = cap_dir / METADATA_FILE
    if metadata.is_file():
        try:
            parsed = loads(metadata.read_text(encoding="utf-8"))
            if type(parsed) is dict:
                data = parsed
        except (OSError, ValueError):
            pass
    try:
        artifact = CapabilityArtifact.from_edn(
            (cap_dir / CAPABILITY_FILE).read_text(encoding="utf-8")
        )
        description = artifact.spec.description
        inputs = artifact.spec.input_schema
        output = artifact.spec.output_schema
        error = None
    except (CapabilityError, OSError) as failure:
        description, inputs, output, error = "", {}, True, str(failure)
    dependencies = data.get("dependencies", {})
    if type(dependencies) is not dict or any(
        type(key) is not str or type(value) is not str for key, value in dependencies.items()
    ):
        dependencies = {}
        error = error or "invalid capability dependency metadata"
    definition_id = data.get("definition-id")
    if definition_id is not None and type(definition_id) is not str:
        definition_id = None
        error = error or "invalid capability definition metadata"
    return CapabilityInfo(
        name=cap_dir.name,
        description=description,
        input_schema=inputs,
        output_schema=output,
        definition_id=definition_id,
        dependencies=dependencies,
        error=error,
        trusted=data.get("trusted") is True,
        author=str(data["author"]) if data.get("author") else None,
        proposal_note=str(data["proposal-note"]) if data.get("proposal-note") else None,
        deprecated=data.get("deprecated") is True,
    )
