"""Retired execution data remains evidence, never a restoration authority."""

import sqlite3
from pathlib import Path

import pytest

from tlon.agents.loader import legacy_work_error
from tlon.agents.program_store import ProgramStore
from tlon.agents.session_journal import SessionJournal
from tlon.agents.session_store import SessionStore
from tlon.base.paths import TlonPaths


def test_pending_legacy_session_scan_is_readonly_and_reports_identity(tmp_path: Path) -> None:
    paths = TlonPaths(tmp_path)
    database = paths.agent_sessions_database_file("a")
    store = SessionStore(database)
    store.append("unfinished-mail", "session/start", {"trigger_content": "evidence"})
    original = database.read_bytes()
    error = legacy_work_error("a", paths)
    assert error is not None
    assert "unfinished-mail" in error and str(database) in error
    assert database.read_bytes() == original
    assert store.events("unfinished-mail")[0]["trigger_content"] == "evidence"


def test_pending_legacy_program_blocks_but_module_entry_does_not(tmp_path: Path) -> None:
    paths = TlonPaths(tmp_path)
    database = paths.agent_program_database_file("a")
    store = ProgramStore(database)
    store.ensure_entry("entry", "{}", "{}", {"modules": {}})
    assert legacy_work_error("a", paths) is None
    store.ensure("old-run", "{}", "{}")
    original = database.read_bytes()
    error = legacy_work_error("a", paths)
    assert error is not None and "old-run" in error and str(database) in error
    assert database.read_bytes() == original
    assert {r.run_id for r in store.pending()} == {"entry", "old-run"}


@pytest.mark.parametrize("database_kind", ["sessions", "program"])
def test_unreadable_legacy_database_is_blocked_not_treated_as_empty(
    tmp_path: Path, database_kind: str
) -> None:
    paths = TlonPaths(tmp_path)
    database = (
        paths.agent_sessions_database_file("a")
        if database_kind == "sessions"
        else paths.agent_program_database_file("a")
    )
    database.parent.mkdir(parents=True)
    database.write_bytes(b"corrupted database evidence")
    error = legacy_work_error("a", paths)
    assert error is not None and str(database) in error and "scan failed" in error
    assert database.read_bytes() == b"corrupted database evidence"


def test_completed_legacy_history_is_readable_without_recreating_execution(tmp_path: Path) -> None:
    paths = TlonPaths(tmp_path)
    database = paths.agent_sessions_database_file("a")
    store = SessionStore(database)
    store.append("done", "session/start", {"context": [{"role": "user", "content": "old"}]})
    store.append("done", "session/end", {"reason": "complete"})
    original = database.read_bytes()
    assert legacy_work_error("a", paths) is None
    with sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True) as connection:
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 2
    assert database.read_bytes() == original


async def test_history_surface_has_no_execution_or_write_interfaces(tmp_path: Path) -> None:
    journal = SessionJournal("a", TlonPaths(tmp_path))
    assert not hasattr(journal, "resume")
    assert not hasattr(journal, "append")
    assert not hasattr(journal, "verify_root")
    assert await journal.read_events("absent") == []
    assert await journal.list_completed() == []
    assert not list(tmp_path.rglob("*.sqlite3"))


async def test_current_module_member_with_legacy_pending_is_not_started(tmp_path: Path) -> None:
    from tlon.agents.member_runtime import MemberRuntime
    from tlon.agents.program_package import validate_program_sources
    from tlon.agents.recovery import RecoveryBlockedError
    from tlon.agents.vocabulary import make_member_builtins
    from tlon.base.events.event_bus import EventBus

    paths = TlonPaths(tmp_path)
    database = paths.agent_sessions_database_file("a")
    SessionStore(database).append("old-action", "session/start", {"trigger_content": "saved"})
    original = database.read_bytes()
    member = MemberRuntime(
        member_id="a",
        name="A",
        event_bus=EventBus(),
        spec=validate_program_sources(
            "{:version 2 :handlers {:boot work.main/handle}}",
            {
                "work/main.tlon": "(ns work.main (:export handle)) (defn handle [event] {:result nil})"
            },
        ),
        builtins=make_member_builtins(),
        tlon_paths=paths,
    )
    with pytest.raises(RecoveryBlockedError, match="old-action"):
        await member.initialize()
    assert member._program_task is None
    assert member.get_diagnostic_error() is not None
    assert database.read_bytes() == original
    assert not paths.agent_program_database_file("a").exists()
    await member.shutdown()
