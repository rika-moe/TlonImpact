"""Behavior prototype library (stage 5 of docs/design/dsl-agents.md).

A prototype is a directory of pure data:

- ``prototype.edn`` — metadata ``{:name :display-name :description :trusted :author}``
- ``behavior.tlon`` — partial AgentSpec override (stage 2 semantics)
- ``IDENTITY.md`` / ``SOUL.md`` / ``RULES.md`` — persona templates

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

Instantiation **copies** rather than references: persona files are copied
and the behavior file is deep-merged with overrides at the EDN data layer,
so an agent's behavior is frozen at creation time.
"""

import shutil
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from tlon.agents.edn import EDNParseError, Keyword, dumps, loads
from tlon.agents.spec import AgentSpec, SpecValidationError
from tlon.base.config import AgentConfig, _yaml_exists, _yaml_save
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
    """

    name: str
    display_name: str
    description: str
    trusted: bool = True
    author: str | None = None


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

    A directory qualifies when it contains a ``behavior.tlon``; metadata
    is read from ``prototype.edn`` when present, with a name-only fallback.
    """
    return _list_from((tlon_paths or paths).prototypes_dir())


def _list_from(root: Path) -> list[PrototypeInfo]:
    """List qualifying prototype directories under ``root``."""
    if not root.exists():
        return []
    result = []
    for proto_dir in sorted(root.iterdir()):
        if not proto_dir.is_dir() or not (proto_dir / BEHAVIOR_FILE).exists():
            continue
        result.append(_read_metadata(proto_dir))
    return result


def get_prototype_info(
    name: str, tlon_paths: TlonPaths | None = None
) -> PrototypeInfo | None:
    """Metadata of a trusted-library prototype, or None when absent."""
    proto_dir = (tlon_paths or paths).prototypes_dir() / name
    if not (proto_dir / BEHAVIOR_FILE).exists():
        return None
    return _read_metadata(proto_dir)


def instantiate(
    prototype_name: str,
    agent_id: str,
    agent_name: str,
    overrides: dict[Any, Any] | None = None,
    proactive: bool = False,
    tlon_paths: TlonPaths | None = None,
) -> AgentConfig:
    """Create an agent from a prototype: copy + partial override.

    Persona files are copied verbatim; the prototype ``behavior.tlon`` is
    parsed and deep-merged with ``overrides`` at the EDN data layer
    (keyword keys preserved), then written as the instance's own behavior
    file. ``agent.yaml`` is written through the same save path as the
    wizard. The new agent takes effect after a server restart — no
    registry hot-reload at this stage.

    Args:
        prototype_name: prototype directory name under ~/.tlon/prototypes/
        agent_id: new agent id (must not already exist)
        agent_name: display name of the new agent
        overrides: parsed-EDN-shaped diff deep-merged over the prototype
            behavior data, e.g. ``{Keyword("loop"): {Keyword("max-iterations"): 5}}``
        proactive: whether the agent responds to unaddressed messages
        tlon_paths: isolated path resolver (tests); defaults to ~/.tlon

    Returns:
        The saved AgentConfig (with ``prototype`` recording the source).

    Raises:
        PrototypeError: prototype missing or agent id already taken.
    """
    p = tlon_paths or paths
    proto_dir = p.prototypes_dir() / prototype_name
    if not (proto_dir / BEHAVIOR_FILE).exists():
        raise PrototypeError(
            f"prototype '{prototype_name}' not found under {p.prototypes_dir()}"
        )
    if _yaml_exists(agent_id, p.tlon_home()):
        raise PrototypeError(f"agent '{agent_id}' already exists")

    # Persona templates: copy, not reference (frozen at creation time)
    config_dir = p.agent_config_dir(agent_id)
    for filename in PERSONA_FILES:
        source = proto_dir / filename
        if source.exists():
            content = source.read_text(encoding="utf-8")
            (config_dir / filename).write_text(content, encoding="utf-8")

    # Behavior: deep-merge overrides at the EDN data layer
    behavior_data = loads((proto_dir / BEHAVIOR_FILE).read_text(encoding="utf-8"))
    if overrides:
        behavior_data = _deep_merge(behavior_data, overrides)
    (config_dir / BEHAVIOR_FILE).write_text(dumps(behavior_data) + "\n", encoding="utf-8")

    # agent.yaml via the same save path as the wizard
    agent = AgentConfig(
        id=agent_id,
        name=agent_name,
        proactive=proactive,
        prototype=prototype_name,
    )
    _yaml_save(agent, p.tlon_home())
    logger.info(
        "prototype_instantiated",
        prototype=prototype_name,
        agent_id=agent_id,
        agent_name=agent_name,
    )
    return agent


def _read_metadata(proto_dir: Any) -> PrototypeInfo:
    """Read ``prototype.edn`` metadata, falling back to the directory name."""
    name = proto_dir.name
    metadata_file = proto_dir / METADATA_FILE
    if not metadata_file.exists():
        return PrototypeInfo(name=name, display_name=name, description="")
    try:
        data = loads(metadata_file.read_text(encoding="utf-8"))
        author = data.get("author")
        return PrototypeInfo(
            name=str(data.get("name") or name),
            display_name=str(data.get("display-name") or data.get("display_name") or name),
            description=str(data.get("description") or ""),
            trusted=bool(data.get("trusted", True)),
            author=str(author) if author else None,
        )
    except Exception as e:
        logger.warning("prototype_metadata_invalid", path=str(metadata_file), error=str(e))
        return PrototypeInfo(name=name, display_name=name, description="")


def _deep_merge(base: Any, override: Any) -> Any:
    """Recursively merge EDN data: maps merge per-key, anything else replaces."""
    if isinstance(base, dict) and isinstance(override, dict):
        merged = dict(base)
        for key, value in override.items():
            merged[key] = _deep_merge(merged[key], value) if key in merged else value
        return merged
    return override


# ==================== Staging area (stage 4a trust gate) ====================


def write_staging_prototype(
    name: str,
    behavior_edn: str,
    persona: dict[str, str] | None = None,
    display_name: str = "",
    description: str = "",
    author_id: str = "",
    tlon_paths: TlonPaths | None = None,
) -> PrototypeInfo:
    """Write an LLM-authored prototype to the staging area (untrusted).

    Validation happens **before** anything touches disk: the behavior text
    must parse as EDN and pass the ``AgentSpec`` schema (``:version``
    required, field types, ``:loop {:mode}`` limited to implemented values).
    The metadata is written with ``:trusted false`` and ``:author`` recording
    the authoring agent, so ``spawnAgent`` refuses it until a human approves
    it in the mirror.

    Args:
        name: prototype directory name (charset-checked, must be unique
            across both the trusted library and the staging area)
        behavior_edn: raw EDN text of the behavior spec
        persona: optional ``{filename: content}`` for IDENTITY.md / SOUL.md /
            RULES.md (only these keys are accepted)
        display_name: human-facing name (falls back to ``name``)
        description: human-facing description
        author_id: id of the agent that authored the spec (audit)
        tlon_paths: isolated path resolver (tests); defaults to ~/.tlon

    Returns:
        Metadata of the staged prototype (``trusted=False``).

    Raises:
        PrototypeError: invalid name, name conflict, or behavior spec failed
            EDN parsing / schema validation (nothing is written in any case)
    """
    p = tlon_paths or paths
    _validate_name(name)
    if (p.prototypes_dir() / name / BEHAVIOR_FILE).exists():
        raise PrototypeError(f"prototype '{name}' already exists in the trusted library")
    staging_dir = p.prototypes_staging_dir() / name
    if staging_dir.exists():
        raise PrototypeError(f"prototype '{name}' already exists in the staging area")

    # Validate first: a broken spec must never reach disk
    try:
        behavior_data = loads(behavior_edn)
    except EDNParseError as e:
        raise PrototypeError(f"behavior EDN parse failed: {e}") from e
    try:
        AgentSpec.from_edn(behavior_data)
    except SpecValidationError as e:
        raise PrototypeError(f"behavior spec validation failed: {e}") from e

    persona = persona or {}
    for filename in persona:
        if filename not in PERSONA_FILES:
            raise PrototypeError(
                f"persona file must be one of {list(PERSONA_FILES)}, got {filename!r}"
            )

    staging_dir.mkdir(parents=True)
    (staging_dir / BEHAVIOR_FILE).write_text(
        behavior_edn if behavior_edn.endswith("\n") else behavior_edn + "\n",
        encoding="utf-8",
    )
    for filename, content in persona.items():
        if content:
            (staging_dir / filename).write_text(content, encoding="utf-8")
    metadata = {
        Keyword("name"): Keyword(name),
        Keyword("display-name"): display_name or name,
        Keyword("description"): description,
        Keyword("trusted"): False,
        Keyword("author"): author_id,
    }
    (staging_dir / METADATA_FILE).write_text(dumps(metadata) + "\n", encoding="utf-8")

    logger.info("staging_prototype_written", name=name, author_id=author_id)
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
    if not (staging_dir / BEHAVIOR_FILE).exists():
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
