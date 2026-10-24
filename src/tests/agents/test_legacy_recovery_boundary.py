"""Retired execution data remains evidence, never a restoration authority."""

from pathlib import Path

from tests.history_helpers import insert_legacy_run
from tlon.agents.loader import legacy_work_error
from tlon.agents.program_store import ProgramStore
from tlon.agents.run_journal import RunJournal
from tlon.base.paths import TlonPaths


def test_pending_legacy_program_blocks_but_module_entry_does_not(tmp_path: Path) -> None:
    paths = TlonPaths(tmp_path)
    database = paths.agent_program_database_file("a")
    store = ProgramStore(database)
    store.ensure_entry("entry", "{}", "{}", {"modules": {}})
    assert legacy_work_error("a", paths) is None
    insert_legacy_run(database, "old-run")
    original = database.read_bytes()
    error = legacy_work_error("a", paths)
    assert error is not None and "old-run" in error and str(database) in error
    assert database.read_bytes() == original
    assert {r.run_id for r in store.pending()} == {"entry", "old-run"}


def test_unreadable_legacy_database_is_blocked_not_treated_as_empty(
    tmp_path: Path
) -> None:
    paths = TlonPaths(tmp_path)
    database = paths.agent_program_database_file("a")
    database.parent.mkdir(parents=True)
    database.write_bytes(b"corrupted database evidence")
    error = legacy_work_error("a", paths)
    assert error is not None and str(database) in error and "scan failed" in error
    assert database.read_bytes() == b"corrupted database evidence"




async def test_history_surface_has_no_execution_or_write_interfaces(tmp_path: Path) -> None:
    journal = RunJournal("a", TlonPaths(tmp_path))
    assert not hasattr(journal, "resume")
    assert not hasattr(journal, "append")
    assert not hasattr(journal, "verify_root")
    assert await journal.read_events("absent") == []
    assert await journal.list_completed() == []
    assert not list(tmp_path.rglob("*.sqlite3"))




async def test_old_agent_session_database_is_ignored_without_modification(tmp_path: Path) -> None:
    paths = TlonPaths(tmp_path)
    archive = paths.agent_dir("a") / "sessions.sqlite3"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"retired unreadable session archive")
    assert legacy_work_error("a", paths) is None
    journal = RunJournal("a", paths)
    assert await journal.list_completed() == []
    assert await journal.read_events("old-session") == []
    assert archive.read_bytes() == b"retired unreadable session archive"
