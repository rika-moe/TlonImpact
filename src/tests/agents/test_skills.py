"""Skill scanning and index rendering (ADR 0010).

Pins the three-layer scan (project > agent > global), the frontmatter
validation rules, name-conflict shadowing, the mtime signature used for
caching, and the ``[可用技能]`` index format. Also pins the DSL side
(``(skills)`` is a valid no-argument section), the built-in skill
seeding (``seed_skills``), and the repo-carried
``explain-decision`` skill's frontmatter/body contract.
"""

from pathlib import Path

import pytest
import yaml

from tlon.agents.member_spec import (
    MemberSpec,
    MemberSpecValidationError,
    validate_member_spec,
)
from tlon.agents.skills import (
    render_skills_index,
    scan_signature,
    scan_skills,
    seed_skills,
)
from tlon.base.paths import TlonPaths
from tlosure.edn import loads

AGENT = "agent1"

BUILTIN_SKILL = "explain-decision"

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
    project_file = _write_skill(_project_root(home), "dup", _skill("dup", "项目版"))
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
            '{:version 1 :rules [{:name "respond" :on :mail-delivered :actions [(run-loop (quote {:context {:sections SECTIONS} :stages [(think)] :until (iteration-limit-reached?) :max-iterations 1 :max-consecutive-errors 1 :max-tokens 1000000 :reflect {:enabled false :interval-minutes 15}}))]}]}'.replace(
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


# ===== 内置 skill 播种与 pin =====


def _builtin_skill_file() -> Path:
    import tlon

    return Path(tlon.__file__).parent / "skills" / BUILTIN_SKILL / "SKILL.md"


class TestBuiltinSkillPin:
    """Repo 内的 explain-decision/SKILL.md 是全员可问责性的落地载体：

    frontmatter 必须能被扫描器解析（name/description 合规），否则播种后
    会被静默跳过、内置 skill 形同虚设。
    """

    def test_builtin_skill_exists_with_valid_frontmatter(self) -> None:
        text = _builtin_skill_file().read_text(encoding="utf-8")
        lines = text.splitlines()
        assert lines[0].strip() == "---"
        end = lines[1:].index("---") + 1
        data = yaml.safe_load("\n".join(lines[1:end]))

        assert data["name"] == BUILTIN_SKILL
        # description 是模型判断「要不要用」的唯一依据：必须写清触发场景。
        assert "为什么" in data["description"]

    def test_builtin_skill_body_teaches_evidence_discipline(self) -> None:
        body = _builtin_skill_file().read_text(encoding="utf-8")
        # 三条证据源与回答纪律的关键词必须都在正文里。
        for keyword in ("traces-list", "trace-read", "memory-search", "mail-search", "无据可查"):
            assert keyword in body


class TestSeedSkills:
    """镜像 seed_prototypes 的语义：幂等、不覆盖、返回拷贝计数。"""

    def test_seeds_builtin_skills_to_global_layer(self, tmp_path: Path) -> None:
        p = TlonPaths(home=tmp_path)

        copied = seed_skills(p)

        assert copied >= 1
        target = p.skills_dir() / BUILTIN_SKILL / "SKILL.md"
        assert target.read_text(encoding="utf-8") == _builtin_skill_file().read_text(
            encoding="utf-8"
        )

    def test_seed_is_idempotent(self, tmp_path: Path) -> None:
        p = TlonPaths(home=tmp_path)

        first = seed_skills(p)
        second = seed_skills(p)

        assert first >= 1
        assert second == 0

    def test_seed_never_overwrites_user_files(self, tmp_path: Path) -> None:
        p = TlonPaths(home=tmp_path)
        user_file = p.skills_dir() / BUILTIN_SKILL / "SKILL.md"
        user_file.parent.mkdir(parents=True)
        user_file.write_text("用户改过的版本\n", encoding="utf-8")
        # 用户自建的 skill 也必须存活。
        user_skill = _write_skill(p.skills_dir(), "mine", _skill("mine", "自建技能"))

        copied = seed_skills(p)

        assert copied == 0
        assert user_file.read_text(encoding="utf-8") == "用户改过的版本\n"
        assert user_skill.exists()

    def test_seeded_builtin_skill_is_scannable(self, tmp_path: Path) -> None:
        p = TlonPaths(home=tmp_path)
        seed_skills(p)

        skills = scan_skills(AGENT, p)

        builtin = [s for s in skills if s.name == BUILTIN_SKILL]
        assert len(builtin) == 1
        assert builtin[0].scope == "global"
        assert builtin[0].path == p.skills_dir() / BUILTIN_SKILL / "SKILL.md"
