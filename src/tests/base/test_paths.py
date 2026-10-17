"""TlonPaths purity contract (docs/reference/config-reference.md): every
accessor resolves a path
and never touches the filesystem — directory creation lives at the write
points. A read intent (retirement inventory, reputation scan, recipient
resolution) must never resurrect a deleted member's directory."""

from pathlib import Path

import pytest

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
    paths.agent_sessions_database_file(agent_id)
    paths.agent_mail_dir(agent_id)
    paths.member_mail_dir(agent_id)
    paths.member_mail_dir("human:alice")
    paths.cron_file()
    paths.agent_identity_file(agent_id)
    paths.agent_soul_file(agent_id)
    paths.agent_rules_file(agent_id)
    paths.agent_contacts_file(agent_id)
    paths.agent_behavior_file(agent_id)
    paths.agent_lessons_file(agent_id)
    paths.skills_dir()
    paths.agent_skills_dir(agent_id)
    paths.agent_workspace_skills_dir(agent_id)
    paths.prototypes_dir()
    paths.prototypes_staging_dir()
    paths.capabilities_dir()
    paths.capabilities_staging_dir()
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


def test_agent_sessions_database_file(tmp_path: Path) -> None:
    paths = TlonPaths(home=tmp_path)
    assert paths.agent_sessions_database_file("dev-001") == (
        tmp_path / "dev-001" / "sessions.sqlite3"
    )


def test_member_mail_dir_maps_human_id(tmp_path: Path) -> None:
    paths = TlonPaths(home=tmp_path)
    assert paths.member_mail_dir("human:x") == tmp_path / "human-x" / "mail"
    assert paths.member_mail_dir("dev-001") == tmp_path / "dev-001" / "mail"


def test_agent_contacts_file_default_is_empty_book(tmp_path: Path) -> None:
    """CONTACTS.md default semantics (ADR 0024, docs/design/identity.md):
    an absent file IS an empty contact book. The helper only resolves the
    path — nothing is seeded eagerly; the read side treats a missing file
    as empty and the write side creates it (with parents) on first write."""
    paths = TlonPaths(home=tmp_path)
    contacts = paths.agent_contacts_file("dev-001")
    assert contacts == tmp_path / "dev-001" / "config" / "CONTACTS.md"
    # No eager creation: resolving the path touches nothing.
    assert not contacts.exists()
    assert not contacts.parent.exists()


class TestTlonHomeResolution:
    """显式设置的 TLON_HOME 必须被尊重或大声失败：指向不存在/非目录时
    报错，绝不静默回落到真实数据根 ~/.tlon（回落方向是 live 社会）。"""

    def test_valid_tlon_home_is_honored(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TLON_HOME", str(tmp_path))
        assert TlonPaths().tlon_home() == tmp_path

    def test_missing_tlon_home_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TLON_HOME", str(tmp_path / "不存在的目录"))
        with pytest.raises(RuntimeError, match="TLON_HOME"):
            TlonPaths()

    def test_tlon_home_pointing_at_file_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        file_path = tmp_path / "a-file"
        file_path.touch()
        monkeypatch.setenv("TLON_HOME", str(file_path))
        with pytest.raises(RuntimeError, match="TLON_HOME"):
            TlonPaths()
