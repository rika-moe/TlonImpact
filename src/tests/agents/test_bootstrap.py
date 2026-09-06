"""First-boot bootstrap — standard team from role prototypes (wizard removal).

Pins the empty-society semantics: no agent.yaml anywhere -> instantiate the
six standard-team members from their role prototypes + write persona files;
any existing agent -> skip (idempotent).
"""

from pathlib import Path

import pytest

from tlon.agents.bootstrap import bootstrap_standard_team
from tlon.agents.loader import load_member_spec
from tlon.base.config import yaml_load_all
from tlon.base.default_team import STANDARD_TEAM_DICTS
from tlon.base.paths import TlonPaths


def _paths(tmp_path) -> TlonPaths:
    return TlonPaths(home=tmp_path)


def test_empty_society_gets_standard_team(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = _paths(tmp_path)
    # load_member_spec reads through the module-level paths singleton —
    # point it at the isolated home (CI runs on a clean ~/.tlon).
    monkeypatch.setattr("tlon.agents.loader.paths", p)

    created = bootstrap_standard_team(p)

    assert created == [role["id"] for role in STANDARD_TEAM_DICTS]
    configs = yaml_load_all(p.tlon_home())
    assert set(configs) == set(created)
    for role in STANDARD_TEAM_DICTS:
        agent_id = role["id"]
        # Lineage: born from the role prototype.
        assert configs[agent_id].prototype == role["prototype"]
        # Behavior genome loads and validates (the boot gate).
        spec = load_member_spec(agent_id)
        assert any(rule.on is None for rule in spec.rules)  # :on :boot -> 常驻
        # Persona written as creation-time data.
        for field, filename in (
            ("identity", "IDENTITY.md"),
            ("profile", "SOUL.md"),
            ("behavior", "RULES.md"),
        ):
            content = role.get(field, "")
            if content:
                file = p.agent_config_dir(agent_id) / filename
                assert file.exists(), f"{agent_id} missing {filename}"
                assert file.read_text(encoding="utf-8").strip() == content.strip()


def test_bootstrap_is_idempotent(tmp_path: Path) -> None:
    p = _paths(tmp_path)

    first = bootstrap_standard_team(p)
    second = bootstrap_standard_team(p)

    assert first == [role["id"] for role in STANDARD_TEAM_DICTS]
    assert second == []  # agents already exist -> skip


def test_existing_agent_skips_bootstrap(tmp_path: Path) -> None:
    p = _paths(tmp_path)
    # A society with any member is not empty: bootstrap must not touch it.
    config_dir = p.agent_config_dir("someone")
    (config_dir / "agent.yaml").write_text(
        "agent:\n  id: someone\n  name: 某人\n", encoding="utf-8"
    )

    created = bootstrap_standard_team(p)

    assert created == []
    assert "someone" in yaml_load_all(p.tlon_home())


def test_bootstrap_seeds_builtin_skills(tmp_path: Path) -> None:
    """Bootstrap seeds the built-in skills alongside the prototypes ():
    the explain-decision skill must land in the global layer even when the
    society itself is not empty."""
    p = _paths(tmp_path)
    config_dir = p.agent_config_dir("someone")
    (config_dir / "agent.yaml").write_text(
        "agent:\n  id: someone\n  name: 某人\n", encoding="utf-8"
    )

    bootstrap_standard_team(p)

    assert (p.skills_dir() / "explain-decision" / "SKILL.md").exists()
