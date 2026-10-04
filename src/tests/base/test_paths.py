"""TlonPaths purity contract (docs/reference/config-reference.md): every
accessor resolves a path
and never touches the filesystem — directory creation lives at the write
points. A read intent (retirement inventory, reputation scan, recipient
resolution) must never resurrect a deleted member's directory."""

from pathlib import Path

from tlon.base.paths import TlonPaths


def _call_every_accessor(paths: TlonPaths, agent_id: str = "dev-001") -> None:
    paths.tlon_home()
    paths.env_file()
    paths.shared_dir()
    paths.agent_dir(agent_id)
    paths.agent_config_dir(agent_id)
    paths.agent_config_file(agent_id)
    paths.agent_memory_dir(agent_id)
    paths.agent_workspace_dir(agent_id)
    paths.agent_tasks_file(agent_id)
    paths.agent_ledger_file(agent_id)
    paths.agent_sessions_dir(agent_id)
    paths.agent_mail_dir(agent_id)
    paths.member_mail_dir(agent_id)
    paths.member_mail_dir("human:alice")
    paths.cron_file()
    paths.agent_identity_file(agent_id)
    paths.agent_soul_file(agent_id)
    paths.agent_rules_file(agent_id)
    paths.agent_behavior_file(agent_id)
    paths.agent_lessons_file(agent_id)
    paths.skills_dir()
    paths.agent_skills_dir(agent_id)
    paths.agent_workspace_skills_dir(agent_id)
    paths.prototypes_dir()
    paths.prototypes_staging_dir()
    paths.memory_config_file(agent_id)
    paths.memory_lessons_file(agent_id)
    paths.traces_dir()
    paths.logs_dir()
    paths.spawn_quota_ledger_file()


def test_accessors_create_nothing(tmp_path: Path) -> None:
    home = tmp_path / "tlon-home"
    home.mkdir()
    _call_every_accessor(TlonPaths(home=home))
    assert list(home.iterdir()) == []


def test_member_mail_dir_maps_human_id(tmp_path: Path) -> None:
    paths = TlonPaths(home=tmp_path)
    assert paths.member_mail_dir("human:x") == tmp_path / "human-x" / "mail"
    assert paths.member_mail_dir("dev-001") == tmp_path / "dev-001" / "mail"
