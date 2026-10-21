"""Validate and read complete member programs without executing user code."""

import hashlib
import json
from dataclasses import replace
from importlib.resources import files
from pathlib import Path

from tlon.agents.member_spec import MemberSpec, MemberSpecValidationError, validate_member_spec
from tlon.agents.vocabulary import make_member_builtins
from tlosure.builtins import Builtin
from tlosure.edn import EDNParseError, loads
from tlosure.modules import ModuleError, ModulePackage


def program_builtins(builtins: dict[str, Builtin] | None = None) -> dict[str, Builtin]:
    """New programs use host operations, never legacy session/control constructors."""
    from tlon.operations.control import control_names

    registry = make_member_builtins() if builtins is None else builtins
    excluded = control_names() | {"run-loop"}
    return {name: value for name, value in registry.items() if name not in excluded}


def program_module_sources(sources: dict[str, str]) -> dict[str, str]:
    """Link host libraries explicitly; authors cannot replace the tlon namespace."""
    if any(path == "tlon.tlon" or path.startswith("tlon/") for path in sources):
        raise MemberSpecValidationError("module namespace tlon is reserved for host libraries")
    library = files("tlon").joinpath("resources", "stdlib", "messages.tlon")
    return {**sources, "tlon/messages.tlon": library.read_text(encoding="utf-8")}


def validate_program_sources(
    behavior: str, modules: dict[str, str], builtins: dict[str, Builtin] | None = None
) -> MemberSpec:
    """Admit a complete source package; no function bodies or effects run."""
    try:
        spec = MemberSpec.from_edn(loads(behavior))
        if spec.version == 1:
            if modules:
                raise MemberSpecValidationError("version 1 does not accept module sources")
            return validate_member_spec(spec, builtins=builtins)
        package = ModulePackage.from_sources(
            program_module_sources(modules), program_builtins(builtins)
        )
        for _, entry in spec.handlers:
            package.entry(entry)
        return replace(spec, module_sources=dict(modules))
    except (ModuleError, EDNParseError) as error:
        raise MemberSpecValidationError(str(error)) from error


def read_module_sources(directory: Path) -> dict[str, str]:
    """Read only regular source files below an explicit module root."""
    if directory.is_symlink():
        raise MemberSpecValidationError("module directory must not be a symlink")
    if not directory.exists():
        return {}
    if not directory.is_dir():
        raise MemberSpecValidationError("modules must be a directory")
    sources: dict[str, str] = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise MemberSpecValidationError("module package cannot contain symlinks")
        if path.is_dir():
            continue
        if not path.is_file() or path.suffix != ".tlon":
            raise MemberSpecValidationError(f"unexpected module file: {path.name}")
        sources[path.relative_to(directory).as_posix()] = path.read_text(encoding="utf-8")
    return sources


def program_digest(behavior: str, modules: dict[str, str]) -> str:
    """Bind approval to exact behavior and complete module content."""
    encoded = json.dumps([behavior, modules], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
