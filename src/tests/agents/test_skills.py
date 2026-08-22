"""Skill scanning and index rendering (ADR 0010).

Pins the three-layer scan (project > agent > global), the frontmatter
validation rules, name-conflict shadowing, the mtime signature used for
caching, and the ``[可用技能]`` index format. Also pins the DSL side:
``(skills)`` is a valid no-argument section.
"""

from pathlib import Path

import pytest

from tlon.agents.edn import loads
from tlon.agents.member_spec import (
    MemberSpec,
    MemberSpecValidationError,
    validate_member_spec,
)
from tlon.agents.skills import (
    render_skills_index,
    scan_signature,
    scan_skills,
)
from tlon.base.paths import TlonPaths

AGENT = "agent1"

VALID_SKILL = """---
name: pdf-extract
description: 从 PDF 提取文本与表格。
---

# PDF 提取

正文指令……
"""


def _write_skill(root: Path, dirname: str, content: str = VALID_SKILL) -> Path:
    skill_dir = root / dirname
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(content, encoding="utf-8")
    return skill_file


def _project_root(home: Path) -> Path:
    return home / AGENT / "workspace" / ".agents" / "skills"


def _agent_root(home: Path) -> Path:
    return home / AGENT / "skills"


def _global_root(home: Path) -> Path:
    return home / "skills"


# ===== scan_skills =====


def test_scan_empty_when_no_skill_dirs(tmp_path: Path) -> None:
    assert scan_skills(AGENT, TlonPaths(home=tmp_path)) == []


def test_scan_collects_distinct_names_in_layer_order(tmp_path: Path) -> None:
    home = tmp_path
    _write_skill(_project_root(home), "b", _skill("b-proj", "项目技能"))
    _write_skill(_agent_root(home), "a", _skill("a-agent", "私人技能"))
    _write_skill(_global_root(home), "c", _skill("c-global", "全局技能"))

    skills = scan_skills(AGENT, TlonPaths(home=home))

    # Layer order (project > agent > global), name-sorted within a layer.
    assert [(s.name, s.scope) for s in skills] == [
        ("b-proj", "project"),
        ("a-agent", "agent"),
        ("c-global", "global"),
    ]


def _skill(name: str, description: str) -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n\n正文。\n"


def test_name_conflict_project_wins(tmp_path: Path) -> None:
    home = tmp_path
    project_file = _write_skill(
        _project_root(home), "dup", _skill("dup", "项目版")
    )
    _write_skill(_global_root(home), "dup", _skill("dup", "全局版"))

    skills = scan_skills(AGENT, TlonPaths(home=home))

    assert len(skills) == 1
    assert skills[0].scope == "project"
    assert skills[0].description == "项目版"
    assert skills[0].path == project_file


def test_agent_beats_global_on_conflict(tmp_path: Path) -> None:
    home = tmp_path
    _write_skill(_agent_root(home), "dup", _skill("dup", "agent 版"))
    _write_skill(_global_root(home), "dup", _skill("dup", "全局版"))

    skills = scan_skills(AGENT, TlonPaths(home=home))

    assert [(s.name, s.scope) for s in skills] == [("dup", "agent")]


def test_skips_skill_without_frontmatter(tmp_path: Path) -> None:
    home = tmp_path
    _write_skill(_global_root(home), "broken", "# 没有 frontmatter\n")

    assert scan_skills(AGENT, TlonPaths(home=home)) == []


def test_skips_skill_missing_required_fields(tmp_path: Path) -> None:
    home = tmp_path
    _write_skill(_global_root(home), "no-desc", "---\nname: only-name\n---\n")

    assert scan_skills(AGENT, TlonPaths(home=home)) == []


def test_skips_skill_with_invalid_yaml(tmp_path: Path) -> None:
    home = tmp_path
    _write_skill(_global_root(home), "bad-yaml", "---\nname: [unclosed\n---\n")

    assert scan_skills(AGENT, TlonPaths(home=home)) == []


def test_ignores_non_skill_entries_and_no_recursion(tmp_path: Path) -> None:
    home = tmp_path
    root = _global_root(home)
    _write_skill(root, "real")
    (root / "README.md").parent.mkdir(parents=True, exist_ok=True)
    (root / "README.md").write_text("not a skill", encoding="utf-8")
    # A nested skill inside a skill dir is not scanned.
    _write_skill(root / "real" / "nested", "inner")

    skills = scan_skills(AGENT, TlonPaths(home=home))

    assert [s.name for s in skills] == ["pdf-extract"]


# ===== scan_signature =====


def test_signature_changes_on_content_and_set_changes(tmp_path: Path) -> None:
    home = tmp_path
    paths = TlonPaths(home=home)
    base = scan_signature(AGENT, paths)

    skill_file = _write_skill(_global_root(home), "one")
    added = scan_signature(AGENT, paths)
    assert added != base

    skill_file.write_text(_skill("one", "改过的描述"), encoding="utf-8")
    edited = scan_signature(AGENT, paths)
    assert edited != added

    # Same content again -> signature stable (no spurious invalidation).
    assert scan_signature(AGENT, paths) == edited


def test_signature_stable_on_repeat(tmp_path: Path) -> None:
    home = tmp_path
    paths = TlonPaths(home=home)
    _write_skill(_global_root(home), "one")
    assert scan_signature(AGENT, paths) == scan_signature(AGENT, paths)


# ===== render_skills_index =====


def test_render_index_format(tmp_path: Path) -> None:
    home = tmp_path
    _write_skill(_global_root(home), "one", _skill("one", "全局技能"))
    skills = scan_skills(AGENT, TlonPaths(home=home))

    index = render_skills_index(skills)

    assert index.startswith("[可用技能]")
    assert f"- one (global): 全局技能 — {skills[0].path}" in index
    assert "read_file" in index


def test_render_index_empty(tmp_path: Path) -> None:
    assert render_skills_index([]) == ""


# ===== DSL section =====


def _spec_with_sections(sections: str) -> MemberSpec:
    return MemberSpec.from_edn(
        loads(
            """\
{:version 1
 :rules [{:name "respond"
          :on :mail-delivered
          :actions []
          :loop {:context {:sections SECTIONS}
                 :stages [(think)]
                 :until (iteration-limit-reached?)
                 :max-iterations 1
                 :max-consecutive-errors 1
                 :reflect {:enabled false :interval-minutes 15}}}]}
""".replace(
                "SECTIONS", sections
            )
        )
    )


def test_skills_section_accepted() -> None:
    spec = _spec_with_sections("[(persona) (skills) (mail-history 5)]")
    validate_member_spec(spec)


def test_skills_section_rejects_arguments() -> None:
    spec = _spec_with_sections("[(skills 3)]")
    with pytest.raises(MemberSpecValidationError, match="takes no arguments"):
        validate_member_spec(spec)
