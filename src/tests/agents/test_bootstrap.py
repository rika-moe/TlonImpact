"""First-boot packaged society and reusable library seeding (ADR 0036)."""

from pathlib import Path

from tlon.agents.bootstrap import bootstrap_society
from tlon.base.config import yaml_load_all
from tlon.base.paths import TlonPaths


def _paths(tmp_path: Path) -> TlonPaths:
    return TlonPaths(home=tmp_path)


def test_bootstrap_seeds_prototypes_and_initial_members(tmp_path: Path) -> None:
    p = _paths(tmp_path)
    bootstrap_society(p)
    assert len(yaml_load_all(p.tlon_home())) == 30
    assert any(p.prototypes_dir().iterdir())


def test_bootstrap_is_idempotent(tmp_path: Path) -> None:
    p = _paths(tmp_path)

    bootstrap_society(p)
    bootstrap_society(p)  # must not raise nor duplicate

    assert len(yaml_load_all(p.tlon_home())) == 30


def test_existing_agent_untouched(tmp_path: Path) -> None:
    """A society with any member is not touched: seeding never writes
    agent configs."""
    p = _paths(tmp_path)
    config_dir = p.agent_config_dir("someone")
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "agent.yaml").write_text(
        "agent:\n  id: someone\n  name: 某人\n", encoding="utf-8"
    )

    bootstrap_society(p)

    assert set(yaml_load_all(p.tlon_home())) == {"someone"}


def test_bootstrap_seeds_builtin_skills(tmp_path: Path) -> None:
    """Bootstrap seeds the built-in skills alongside the prototypes ():
    the explain-decision skill must land in the global layer."""
    p = _paths(tmp_path)

    bootstrap_society(p)

    assert (p.skills_dir() / "explain-decision" / "SKILL.md").exists()
