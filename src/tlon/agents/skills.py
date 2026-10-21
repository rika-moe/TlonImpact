"""Skill scanning and index rendering (ADR 0010, docs/design/skills.md).

A skill is a directory carrying a ``SKILL.md`` (Agent Skills open
standard): YAML frontmatter with the required ``name``/``description``,
then a Markdown body of instructions (optionally with ``scripts/`` /
``references/`` alongside). The engine scans three layers — project
(``workspace/.agents/skills``, bind-mounted into the container), agent
(``~/.tlon/{agent_id}/skills``), global (``~/.tlon/skills``) — and
renders only the *index* (one line per skill) into the context via the
``(skills)`` DSL section. The model loads the body itself with
``file-read`` (progressive disclosure): no engine-side body injection,
no ``use_skill`` tool.

Name conflicts resolve project > agent > global: the layers are scanned
in that order and the first occurrence of a name wins; later duplicates
are skipped with a warning.

Built-in skills ship inside the package (``tlon/skills/``) and are
seeded to the global layer ``~/.tlon/skills/`` on startup by
:func:`seed_skills` (same mechanism as ``seed_prototypes``); seeding
never overwrites existing files, so user edits and user-added skills
survive re-seeding.
"""

from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Literal

import yaml

from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths

logger = get_logger(__name__)

#: The file that anchors a skill directory.
SKILL_FILE = "SKILL.md"

#: Resource package carrying the built-in skills (ADR 0010).
SKILL_PACKAGE = "tlon.skills"

Scope = Literal["project", "agent", "global"]

#: The skill-landscape cache signature: per layer, either None (root
#: missing) or the sorted ``(skill-dir-name, SKILL.md mtime)`` entries.
Signature = tuple[tuple[str, tuple[tuple[str, float], ...] | None], ...]


@dataclass(frozen=True)
class SkillInfo:
    """One discovered skill: frontmatter fields plus the SKILL.md host path.

    ``path`` is the host absolute path — the index hands it to the model
    for ``file-read`` (project-level skills are the same file as the
    container's ``/workspace/.agents/skills/...`` via the bind mount).
    """

    name: str
    description: str
    path: Path
    scope: Scope


def seed_skills(tlon_paths: TlonPaths) -> int:
    """Copy built-in skills to the global layer ``~/.tlon/skills/`` (idempotent).

    Mirrors ``seed_prototypes`` (``tlon.agents.prototypes``): existing files
    are never overwritten, so user edits and user-added skills survive
    re-seeding.

    Returns:
        Number of files copied (0 when everything already exists).
    """
    target_root = tlon_paths.skills_dir()
    copied = 0
    for entry in resources.files(SKILL_PACKAGE).iterdir():
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
    logger.info("skills_seeded", copied=copied, dir=str(target_root))
    return copied


def _layer_dirs(agent_id: str, tlon_paths: TlonPaths) -> list[tuple[Scope, Path]]:
    """The three skill layers in precedence order (first wins)."""
    return [
        ("project", tlon_paths.agent_workspace_skills_dir(agent_id)),
        ("agent", tlon_paths.agent_skills_dir(agent_id)),
        ("global", tlon_paths.skills_dir()),
    ]


def scan_signature(agent_id: str, tlon_paths: TlonPaths) -> Signature:
    """Stat-only signature of the skill landscape, for cache invalidation.

    Covers skill-dir additions/removals and SKILL.md content changes
    (mtime). Frontmatter parsing happens only when this signature
    changes — an unchanged landscape costs no file reads.
    """
    signature: list[tuple[str, tuple[tuple[str, float], ...] | None]] = []
    for scope, root in _layer_dirs(agent_id, tlon_paths):
        if not root.is_dir():
            signature.append((scope, None))
            continue
        entries: list[tuple[str, float]] = []
        for child in sorted(root.iterdir()):
            skill_file = child / SKILL_FILE
            if child.is_dir() and skill_file.is_file():
                entries.append((child.name, skill_file.stat().st_mtime))
        signature.append((scope, tuple(entries)))
    return tuple(signature)


def scan_skills(agent_id: str, tlon_paths: TlonPaths) -> list[SkillInfo]:
    """Scan the three layers and parse every SKILL.md frontmatter.

    Only direct children of each layer root are considered (no
    recursion). A missing layer root is simply empty. A SKILL.md whose
    frontmatter is missing, unparseable, or lacks
    ``name``/``description`` is skipped with a warning.
    """
    skills: list[SkillInfo] = []
    seen: set[str] = set()
    for scope, root in _layer_dirs(agent_id, tlon_paths):
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            skill_file = child / SKILL_FILE
            if not (child.is_dir() and skill_file.is_file()):
                continue
            info = _parse_skill(skill_file, scope)
            if info is None:
                continue
            if info.name in seen:
                logger.warning("skill_shadowed", name=info.name, scope=scope, path=str(skill_file))
                continue
            seen.add(info.name)
            skills.append(info)
    return skills


def render_skills_index(skills: list[SkillInfo]) -> str:
    """Render the ``[可用技能]`` index block; empty string when no skills."""
    if not skills:
        return ""
    lines = ["[可用技能]", ""]
    for skill in skills:
        lines.append(f"- {skill.name} ({skill.scope}): {skill.description} — {skill.path}")
    lines.append("")
    lines.append("需要某个技能时，用 file-read 读取其 SKILL.md 获取完整指令。")
    return "\n".join(lines)


def _parse_skill(skill_file: Path, scope: Scope) -> SkillInfo | None:
    """Parse one SKILL.md's frontmatter; None (logged) on any defect."""
    try:
        text = skill_file.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("skill_read_failed", path=str(skill_file), error=str(e))
        return None
    frontmatter = _extract_frontmatter(text)
    if frontmatter is None:
        logger.warning("skill_frontmatter_missing", path=str(skill_file))
        return None
    try:
        data = yaml.safe_load(frontmatter)
    except yaml.YAMLError as e:
        logger.warning("skill_frontmatter_invalid", path=str(skill_file), error=str(e))
        return None
    if not isinstance(data, dict):
        logger.warning("skill_frontmatter_invalid", path=str(skill_file))
        return None
    name = data.get("name")
    description = data.get("description")
    if (
        not isinstance(name, str)
        or not name.strip()
        or not isinstance(description, str)
        or not description.strip()
    ):
        logger.warning("skill_frontmatter_incomplete", path=str(skill_file))
        return None
    return SkillInfo(
        name=name.strip(),
        description=description.strip(),
        path=skill_file,
        scope=scope,
    )


def _extract_frontmatter(text: str) -> str | None:
    """The YAML block between the leading ``---`` fence and the next one."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[1:i])
    return None
