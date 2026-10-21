"""Trusted artifacts compile into the shared operation directory.

Approval binds the complete dependency content. A broken or stale definition is
excluded with its dependents; unrelated words remain loadable.
"""

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from tlon.agents.capabilities.spec import CapabilityArtifact, CapabilityError
from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths
from tlon.operations.composite import CompositeCatalog, compile_composites
from tlon.operations.core import Operation
from tlosure.edn import loads

if TYPE_CHECKING:
    from tlosure.builtins import Builtin

logger = get_logger(__name__)


def read_trusted_artifacts(
    tlon_paths: TlonPaths,
) -> tuple[dict[str, CapabilityArtifact], dict[str, dict[str, Any]], dict[str, str]]:
    artifacts: dict[str, CapabilityArtifact] = {}
    approvals: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}
    root = tlon_paths.capabilities_dir()
    if not root.exists():
        return artifacts, approvals, errors
    for directory in sorted(root.iterdir()):
        if not directory.is_dir() or not (directory / "capability.tlon").is_file():
            continue
        try:
            artifact = CapabilityArtifact.from_edn(
                (directory / "capability.tlon").read_text(encoding="utf-8")
            )
            if artifact.name != directory.name:
                raise CapabilityError("artifact name must match its directory")
            metadata = loads((directory / "meta.tlon").read_text(encoding="utf-8"))
            if type(metadata) is not dict or metadata.get("trusted") is not True:
                raise CapabilityError("capability has not been approved")
            artifacts[artifact.name] = artifact
            approvals[artifact.name] = metadata
        except (CapabilityError, OSError, ValueError) as error:
            errors[directory.name] = str(error)
    return artifacts, approvals, errors


def load_capability_catalog(
    tlon_paths: TlonPaths, *, hosts: Iterable[Operation] | None = None, reserved: Iterable[str] = ()
) -> CompositeCatalog:
    from tlon.operations.definitions import host_operation_definitions

    reserved = set(reserved) | reserved_capability_names()
    definitions = tuple(host_operation_definitions() if hosts is None else hosts)
    artifacts, approvals, errors = read_trusted_artifacts(tlon_paths)
    for name in set(artifacts) & set(reserved):
        errors[name] = "capability name collides with reserved vocabulary"
        artifacts.pop(name)
    catalog = compile_composites(
        (artifact.spec for artifact in artifacts.values()), definitions, strict=False
    )
    errors.update(catalog.errors)
    accepted = dict(artifacts)
    for name, definition in catalog.definitions.items():
        metadata = approvals[name]
        if (
            metadata.get("definition-id") != definition.definition_id
            or metadata.get("dependencies") != definition.dependency_ids
        ):
            errors[name] = "approved dependency fingerprint changed; explicit reapproval required"
            accepted.pop(name, None)
    # Removing a stale node makes every dependent fail normal graph validation.
    catalog = compile_composites(
        (artifact.spec for artifact in accepted.values()), definitions, strict=False
    )
    errors.update(catalog.errors)
    catalog.errors.update(errors)
    for name, error in errors.items():
        logger.error("capability_operation_rejected", name=name, error=error)
    return catalog


def reserved_capability_names() -> set[str]:
    """Host vocabulary and implicit Behavior bindings share one namespace."""
    from tlon.agents.vocabulary import ACTION_SCOPE, RULE_SCOPE, make_member_builtins

    return set(make_member_builtins()) | set(RULE_SCOPE) | set(ACTION_SCOPE)


def capability_validation_builtins(
    tlon_paths: TlonPaths, *, base: dict[str, "Builtin"] | None = None
) -> dict[str, "Builtin"]:
    """Project the approved directory for syntax admission without execution.

    Templates and Behavior proposals know valid operation names. A concrete
    member still validates again against its actual assembled operations.
    """
    from tlon.agents.vocabulary import make_member_builtins
    from tlon.operations.core import CallContext, OperationExecutor, OperationRegistry
    from tlon.operations.dsl import DSLAdapter

    builtins = make_member_builtins() if base is None else dict(base)
    catalog = load_capability_catalog(tlon_paths, reserved=builtins)
    executor = OperationExecutor(
        OperationRegistry(catalog.operations),
        CallContext("validation", "validation", "validation"),
    )
    return {**builtins, **DSLAdapter(executor).builtins()}
