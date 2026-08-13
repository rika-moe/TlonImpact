"""Behavior prototype library (stage 5 of docs/design/dsl-agents.md).

A prototype is a directory of pure data:

- ``prototype.edn`` — metadata ``{:name :display-name :description :trusted :author}``
- ``behavior.tlon`` — a complete (total) member spec, unified rules form
  (``{:version 1 :rules [...]}``, phase C — the old ``script.tlon``
  ScriptSpec file is gone)
- ``IDENTITY.md`` / ``SOUL.md`` / ``RULES.md`` — persona templates
  (optional; only meaningful for cognitive members)

A directory qualifies as a prototype when it contains ``behavior.tlon``.
The member type (cognitive ``react`` vs deterministic ``script``) is no
longer a metadata keyword — it is **derived** from the spec's rules
(any rule with a ``:loop`` ⇒ react), so ``prototype.edn`` never declares
a runtime.

Built-in prototypes ship inside the package (``tlon/prototypes/``) and are
seeded to ``~/.tlon/prototypes/`` on startup; seeding never overwrites
existing files, so users may edit or add prototypes. ``list_prototypes``
reads the seeded directory (user modifications stay visible).

Trust model (stage 4a, the "code signing" analogy): package-seeded and
user-installed prototypes are trusted (``:trusted true``, directly
spawnable). LLM-authored prototypes are written to the staging area
(``~/.tlon/prototypes-staging/``) with ``:trusted false`` and can only be
spawned after a human approves them in the mirror, which moves them into
the trusted library.

A ``behavior.tlon`` may declare a top-level ``:parameters`` map (a
write-side template, e.g. relay's ``{:to {:type :string-list}}``) and
reference the variables as ``${to}`` inside rule forms. Instantiation
renders the template with the caller's values before writing the instance
file; the runtime never sees placeholders.

Instantiation **copies** rather than references: persona files and the
behavior file are copied verbatim, so an agent's behavior is frozen at
creation time. There are no overrides — a spec is total, and the
prototype's file is the whole behavior.
"""

import shutil
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from tlon.agents.behavior_template import (
    BehaviorTemplateError,
    ParameterSpec,
    analyze,
    render_template,
)
from tlon.agents.edn import EDNParseError, Keyword, dumps, loads
from tlon.agents.member_spec import (
    MemberSpec,
    MemberSpecValidationError,
    member_type,
)
from tlon.base.config import AgentConfig, yaml_exists, yaml_save
from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths, paths

logger = get_logger(__name__)

PROTOTYPE_PACKAGE = "tlon.prototypes"
METADATA_FILE = "prototype.edn"
BEHAVIOR_FILE = "behavior.tlon"
PERSONA_FILES = ("IDENTITY.md", "SOUL.md", "RULES.md")


class PrototypeError(ValueError):
    """Prototype lookup or instantiation failure."""


@dataclass(frozen=True)
class PrototypeInfo:
    """Metadata of one behavior prototype.

    ``trusted`` defaults to True for user-installed prototypes (handwritten
    implies trusted); only the staging area writes ``:trusted false``.
    ``runtime`` is **derived** from the behavior file's rules (phase C: any
    rule with a ``:loop`` ⇒ ``"react"``, else ``"script"``) — it is never a
    metadata keyword anymore. ``parameters`` (phase F) is the template's
    declared ``:parameters`` (empty for a concrete behavior), so callers
    can prompt for values before instantiation.
    """

    name: str
    display_name: str
    description: str
    trusted: bool = True
    author: str | None = None
    runtime: str = "react"
    parameters: tuple[ParameterSpec, ...] = ()

    @property
    def has_parameters(self) -> bool:
        """Whether instantiation requires template parameter values."""
        return bool(self.parameters)


def seed_prototypes(tlon_paths: TlonPaths | None = None) -> int:
    """Copy built-in prototypes to ``~/.tlon/prototypes/`` (idempotent).

    Existing files are never overwritten: user edits and user-added
    prototypes survive re-seeding.

    Returns:
        Number of files copied (0 when everything already exists).
    """
    p = tlon_paths or paths
    target_root = p.prototypes_dir()
    copied = 0
    for entry in resources.files(PROTOTYPE_PACKAGE).iterdir():
        # __pycache__ appears when running from a source tree
        if not entry.is_dir() or entry.name.startswith(("_", ".")):
            continue
        target_dir = target_root / entry.name
        for resource in entry.iterdir():
            if not resource.is_file() or resource.name.startswith(("_", ".")):
                continue
            target = target_dir / resource.name
            if target.exists():
                continue
            target_dir.mkdir(parents=True, exist_ok=True)
            target.write_bytes(resource.read_bytes())
            copied += 1
    logger.info("prototypes_seeded", copied=copied, dir=str(target_root))
    return copied


def list_prototypes(tlon_paths: TlonPaths | None = None) -> list[PrototypeInfo]:
    """List prototypes from ``~/.tlon/prototypes/`` (not the package).

    A directory qualifies when it contains a ``behavior.tlon``; metadata is
    read from ``prototype.edn`` when present, with a name-only fallback.
    """
    return _list_from((tlon_paths or paths).prototypes_dir())


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


def _is_prototype_dir(proto_dir: Path) -> bool:
    """A directory qualifies when it carries the unified behavior file."""
    return (proto_dir / BEHAVIOR_FILE).exists()


def get_prototype_info(
    name: str, tlon_paths: TlonPaths | None = None
) -> PrototypeInfo | None:
    """Metadata of a trusted-library prototype, or None when absent."""
    proto_dir = (tlon_paths or paths).prototypes_dir() / name
    if not _is_prototype_dir(proto_dir):
        return None
    return _read_metadata(proto_dir)


def instantiate(
    prototype_name: str,
    agent_id: str,
    agent_name: str,
    params: dict[str, Any] | None = None,
    tlon_paths: TlonPaths | None = None,
) -> AgentConfig:
    """Create an agent from a prototype: copy, no merging.

    Persona files are copied verbatim; the prototype ``behavior.tlon`` —
    a complete, total spec in the unified rules form — is copied verbatim
    as the instance's own behavior file.

    When the prototype's spec declares ``:parameters`` (a parameterized
    write-side template), ``params`` must provide concrete values; the
    file is rendered with those values before writing to disk. Otherwise
    ``params`` must be empty.

    ``agent.yaml`` is written through the same save path as the wizard.
    The new agent takes effect without a restart: the caller
    (wizard / registry spawn) hot-adopts it — the wizard notifies the
    running server (POST ``/admin/config/agents/{id}/reload`` →
    ``AgentRegistry.adopt_agent``); when no server is running, it takes
    effect after the next server restart.

    Args:
        prototype_name: prototype directory name under ~/.tlon/prototypes/
        agent_id: new agent id (must not already exist)
        agent_name: display name of the new agent
        params: template parameter values (required when the prototype
            declares ``:parameters``; must be empty otherwise)
        tlon_paths: isolated path resolver (tests); defaults to ~/.tlon

    Returns:
        The saved AgentConfig (with ``prototype`` recording the source).

    Raises:
        PrototypeError: prototype missing, agent id already taken, or
            required params missing / invalid.
    """
    p = tlon_paths or paths
    proto_dir = p.prototypes_dir() / prototype_name
    if not _is_prototype_dir(proto_dir):
        raise PrototypeError(
            f"prototype '{prototype_name}' not found under {p.prototypes_dir()}"
        )
    if yaml_exists(agent_id, p.tlon_home()):
        raise PrototypeError(f"agent '{agent_id}' already exists")

    behavior_file = proto_dir / BEHAVIOR_FILE
    raw_text = behavior_file.read_text(encoding="utf-8")

    info = analyze(raw_text)
    if info.has_parameters:
        if params is None:
            raise PrototypeError(
                f"prototype '{prototype_name}' declares "
                f":parameters {sorted(info.placeholders)!r} — "
                f"params must be provided at instantiation"
            )
        try:
            behavior_text = render_template(raw_text, params)
        except BehaviorTemplateError as e:
            raise PrototypeError(
                f"prototype '{prototype_name}' parameter error: {e}"
            ) from e
    else:
        if params:
            raise PrototypeError(
                f"prototype '{prototype_name}' does not declare "
                f":parameters, but params were provided"
            )
        behavior_text = raw_text

    # Persona templates and behavior: copy, not reference (frozen at
    # creation time; a spec is total, so there is nothing to merge)
    config_dir = p.agent_config_dir(agent_id)
    (config_dir / BEHAVIOR_FILE).write_text(behavior_text, encoding="utf-8")
    for filename in PERSONA_FILES:
        source = proto_dir / filename
        if source.exists():
            (config_dir / filename).write_text(
                source.read_text(encoding="utf-8"), encoding="utf-8"
            )

    # agent.yaml via the same save path as the wizard
    agent = AgentConfig(
        id=agent_id,
        name=agent_name,
        prototype=prototype_name,
    )
    yaml_save(agent, p.tlon_home())
    logger.info(
        "prototype_instantiated",
        prototype=prototype_name,
        agent_id=agent_id,
        agent_name=agent_name,
    )
    return agent


def _derive_runtime(proto_dir: Path) -> str:
    """Derive a prototype's member type from its behavior file's rules.

    ``runtime`` is a derived value in phase C — the spec decides whether
    this prototype produces a cognitive (react) or deterministic (script)
    member. A template's stand-ins are structurally valid, so the derived
    type is stable regardless of the eventual parameter values. Anything
    unparseable falls back to ``"react"`` (metadata read must not fail a
    listing).
    """
    behavior_file = proto_dir / BEHAVIOR_FILE
    if not behavior_file.exists():
        return "react"
    try:
        template = analyze(behavior_file.read_text(encoding="utf-8"))
        member = MemberSpec.from_edn(loads(template.standin_text))
    except Exception:
        return "react"
    return member_type(member)


def _read_parameters(proto_dir: Any) -> tuple[ParameterSpec, ...]:
    """The behavior template's declared ``:parameters`` (empty if concrete).

    Metadata reading must never fail a listing: an unparseable behavior
    yields no parameters (``_derive_runtime`` already falls back to
    ``"react"`` the same way).
    """
    behavior_file = proto_dir / BEHAVIOR_FILE
    if not behavior_file.exists():
        return ()
    try:
        return analyze(behavior_file.read_text(encoding="utf-8")).parameters
    except Exception as e:
        logger.warning("prototype_parameters_unreadable", path=str(behavior_file), error=str(e))
        return ()


def _read_metadata(proto_dir: Any) -> PrototypeInfo:
    """Read ``prototype.edn`` metadata, falling back to the directory name."""
    name = proto_dir.name
    parameters = _read_parameters(proto_dir)
    metadata_file = proto_dir / METADATA_FILE
    if not metadata_file.exists():
        return PrototypeInfo(
            name=name,
            display_name=name,
            description="",
            runtime=_derive_runtime(proto_dir),
            parameters=parameters,
        )
    try:
        data = loads(metadata_file.read_text(encoding="utf-8"))
        author = data.get("author")
        return PrototypeInfo(
            name=str(data.get("name") or name),
            display_name=str(data.get("display-name") or data.get("display_name") or name),
            description=str(data.get("description") or ""),
            trusted=bool(data.get("trusted", True)),
            author=str(author) if author else None,
            runtime=_derive_runtime(proto_dir),
            parameters=parameters,
        )
    except Exception as e:
        logger.warning("prototype_metadata_invalid", path=str(metadata_file), error=str(e))
        return PrototypeInfo(name=name, display_name=name, description="")


# ==================== Staging area (stage 4a trust gate) ====================


def write_staging_prototype(
    name: str,
    behavior_edn: str,
    persona: dict[str, str] | None = None,
    display_name: str = "",
    description: str = "",
    author_id: str = "",
    spec_type: str = "react",
    tlon_paths: TlonPaths | None = None,
) -> PrototypeInfo:
    """Write an LLM-authored prototype to the staging area (untrusted).

    Validation happens **before** anything touches disk.

    ``spec_type`` selects the **template**, not the file format (phase C
    unified the format): ``"react"`` (default) writes persona files
    alongside the behavior; ``"script"`` is a persona-less deterministic
    daemon. Either way the output is a single unified rules-form
    ``behavior.tlon`` — the old ``script.tlon`` ScriptSpec file and its
    ``:runtime :script`` metadata keyword are gone.

    The behavior text may be a write-side template: when it declares
    ``:parameters`` and references ``${var}`` placeholders, the declaration
    is validated and the text is checked structurally (placeholders
    substituted to type stand-ins). A concrete (non-template) text must
    parse as EDN and pass the unified ``MemberSpec`` schema.

    The metadata is written with ``:trusted false`` and ``:author`` recording
    the authoring agent, so ``spawnAgent`` refuses it until a human approves
    it in the mirror.

    Args:
        name: prototype directory name (charset-checked, must be unique
            across both the trusted library and the staging area)
        behavior_edn: raw EDN text of the unified behavior spec
        persona: optional ``{filename: content}`` for IDENTITY.md / SOUL.md /
            RULES.md (only these keys are accepted; ignored for script)
        display_name: human-facing name (falls back to ``name``)
        description: human-facing description
        author_id: id of the agent that authored the spec (audit)
        spec_type: ``"react"`` (persona + cognitive behavior) or
            ``"script"`` (persona-less deterministic behavior)
        tlon_paths: isolated path resolver (tests); defaults to ~/.tlon

    Returns:
        Metadata of the staged prototype (``trusted=False``).

    Raises:
        PrototypeError: invalid name, name conflict, or spec failed
            template / EDN / schema validation (nothing is written in any
            case)
    """
    if spec_type not in ("react", "script"):
        raise PrototypeError(
            f"spec_type must be 'react' or 'script', got {spec_type!r}"
        )
    p = tlon_paths or paths
    _validate_name(name)

    is_script = spec_type == "script"

    if (p.prototypes_dir() / name / BEHAVIOR_FILE).exists():
        raise PrototypeError(f"prototype '{name}' already exists in the trusted library")
    staging_dir = p.prototypes_staging_dir() / name
    if staging_dir.exists():
        raise PrototypeError(f"prototype '{name}' already exists in the staging area")

    # Validate first: a broken spec must never reach disk. The write-side
    # gate is structural — placeholder stand-ins are concrete, so the
    # unified schema check runs on the same text a runtime would load.
    spec_text = behavior_edn if behavior_edn.endswith("\n") else behavior_edn + "\n"
    try:
        template = analyze(spec_text)
        MemberSpec.from_edn(loads(template.standin_text))
    except BehaviorTemplateError as e:
        raise PrototypeError(f"behavior template error: {e}") from e
    except EDNParseError as e:
        raise PrototypeError(f"behavior EDN parse failed: {e}") from e
    except MemberSpecValidationError as e:
        raise PrototypeError(f"behavior spec validation failed: {e}") from e

    persona = persona or {}
    if not is_script:
        for filename in persona:
            if filename not in PERSONA_FILES:
                raise PrototypeError(
                    f"persona file must be one of {list(PERSONA_FILES)}, got {filename!r}"
                )

    staging_dir.mkdir(parents=True)

    (staging_dir / BEHAVIOR_FILE).write_text(spec_text, encoding="utf-8")

    if not is_script:
        for filename, content in persona.items():
            if content:
                (staging_dir / filename).write_text(content, encoding="utf-8")

    metadata: dict[Any, Any] = {
        Keyword("name"): Keyword(name),
        Keyword("display-name"): display_name or name,
        Keyword("description"): description,
        Keyword("trusted"): False,
        Keyword("author"): author_id,
    }
    (staging_dir / METADATA_FILE).write_text(dumps(metadata) + "\n", encoding="utf-8")

    logger.info(
        "staging_prototype_written",
        name=name,
        author_id=author_id,
        spec_type=spec_type,
    )
    return _read_metadata(staging_dir)


def list_staging_prototypes(tlon_paths: TlonPaths | None = None) -> list[PrototypeInfo]:
    """List prototypes awaiting approval in ``~/.tlon/prototypes-staging/``."""
    return _list_from((tlon_paths or paths).prototypes_staging_dir())


def approve_prototype(name: str, tlon_paths: TlonPaths | None = None) -> PrototypeInfo:
    """Promote a staged prototype to the trusted library (human approval).

    Moves the directory to ``~/.tlon/prototypes/`` and rewrites its
    metadata with ``:trusted true``; the ``:author`` record is kept.

    Raises:
        PrototypeError: invalid name, nothing staged under that name, or the
            trusted library already has a prototype with that name
    """
    p = tlon_paths or paths
    _validate_name(name)
    staging_dir = p.prototypes_staging_dir() / name
    if not _is_prototype_dir(staging_dir):
        raise PrototypeError(f"no staged prototype named '{name}'")
    target_dir = p.prototypes_dir() / name
    if target_dir.exists():
        raise PrototypeError(f"prototype '{name}' already exists in the trusted library")

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
    data.setdefault(Keyword("display-name"), name)
    data.setdefault(Keyword("description"), "")
    metadata_file.write_text(dumps(data) + "\n", encoding="utf-8")

    logger.info("prototype_approved", name=name, author=str(data.get("author") or ""))
    return _read_metadata(target_dir)


def reject_prototype(name: str, tlon_paths: TlonPaths | None = None) -> None:
    """Delete a staged prototype (human rejection).

    Path-injection guard mirrors the contractor home deletion: the name must
    pass the charset check and resolve strictly inside the staging root.

    Raises:
        PrototypeError: invalid name or nothing staged under that name
    """
    p = tlon_paths or paths
    _validate_name(name)
    staging_root = p.prototypes_staging_dir().resolve()
    target = (staging_root / name).resolve()
    if target.parent != staging_root:
        raise PrototypeError(f"prototype name '{name}' resolves outside the staging area")
    if not target.is_dir():
        raise PrototypeError(f"no staged prototype named '{name}'")
    shutil.rmtree(target)
    logger.info("prototype_rejected", name=name)


def _validate_name(name: str) -> None:
    """Prototype names share the AgentConfig charset: alnum plus ``-``/``_``."""
    if not name or not name.replace("-", "").replace("_", "").isalnum():
        raise PrototypeError(
            f"invalid prototype name {name!r}: only letters, digits, '-' and '_' allowed"
        )
