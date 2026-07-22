"""Behavior prototype library (stage 5 of docs/design/dsl-agents.md).

A prototype is a directory of pure data:

- ``prototype.edn`` — metadata ``{:name :display-name :description}``
- ``behavior.tlon`` — partial AgentSpec override (stage 2 semantics)
- ``IDENTITY.md`` / ``SOUL.md`` / ``RULES.md`` — persona templates

Built-in prototypes ship inside the package (``tlon/prototypes/``) and are
seeded to ``~/.tlon/prototypes/`` on startup; seeding never overwrites
existing files, so users may edit or add prototypes. ``list_prototypes``
reads the seeded directory (user modifications stay visible).

Instantiation **copies** rather than references: persona files are copied
and the behavior file is deep-merged with overrides at the EDN data layer,
so an agent's behavior is frozen at creation time.
"""

from dataclasses import dataclass
from importlib import resources
from typing import Any

from tlon.agents.edn import dumps, loads
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
    """Metadata of one behavior prototype."""

    name: str
    display_name: str
    description: str


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
    root = (tlon_paths or paths).prototypes_dir()
    if not root.exists():
        return []
    result = []
    for proto_dir in sorted(root.iterdir()):
        if not proto_dir.is_dir() or not (proto_dir / BEHAVIOR_FILE).exists():
            continue
        result.append(_read_metadata(proto_dir))
    return result


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
        return PrototypeInfo(
            name=str(data.get("name") or name),
            display_name=str(data.get("display-name") or data.get("display_name") or name),
            description=str(data.get("description") or ""),
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
