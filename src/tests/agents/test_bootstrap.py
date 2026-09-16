"""First-boot seeding (ADR 0016).

Pins the empty-society semantics: bootstrap seeds the built-in prototype
library and skills only — members are NEVER seeded. The first boot is an
empty society; the human creates members via the WebUI / admin API and
picks a model per member (there is no process-level model fallback).
"""

from pathlib import Path

from tlon.agents.bootstrap import bootstrap_society
from tlon.base.config import yaml_load_all
from tlon.base.paths import TlonPaths


def _paths(tmp_path) -> TlonPaths:
    return TlonPaths(home=tmp_path)


def test_bootstrap_seeds_prototypes_but_never_members(tmp_path: Path) -> None:
    """An empty society stays empty: no agent.yaml is written, but the
    prototype library is there for the human to instantiate from."""
    p = _paths(tmp_path)

    bootstrap_society(p)

    assert yaml_load_all(p.tlon_home()) == {}
    assert any(p.prototypes_dir().iterdir())


def test_bootstrap_is_idempotent(tmp_path: Path) -> None:
    p = _paths(tmp_path)

    bootstrap_society(p)
    bootstrap_society(p)  # must not raise nor duplicate

    assert yaml_load_all(p.tlon_home()) == {}


def test_existing_agent_untouched(tmp_path: Path) -> None:
    """A society with any member is not touched: seeding never writes
    agent configs."""
    p = _paths(tmp_path)
    config_dir = p.agent_config_dir("someone")
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
