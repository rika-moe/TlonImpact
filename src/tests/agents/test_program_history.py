"""Program reading views rebuild exclusively from committed execution facts."""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tlon.agents.program_store import ProgramStore
from tlon.agents.session_journal import SessionJournal
from tlon.base.paths import TlonPaths
from tlon.memory.models import build_session_projection
from tlon.operations.invocation import Invocation, InvocationOutcome


def _entry(store: ProgramStore, run_id: str) -> None:
    store.ensure_entry(
        run_id, "{}", '{"payload" {"mail" {"__tlon_mail__" {"content" "hello" "id" "mail-1"}}}}', {}
    )


def _commit(store: ProgramStore, run_id: str, name: str, value: object) -> None:
    arguments = {"request": {"messages": [{"role": "user", "content": "question"}]}}
    invocation = Invocation(
        name + run_id, run_id, (name,), name, json.dumps(arguments), "test", store.build_id
    )
    store.commit_invocation(invocation, InvocationOutcome(True, value=value))


@pytest.mark.parametrize("disk", [False, True])
async def test_rebuild_mixed_history_without_execution(tmp_path: Path, disk: bool) -> None:
    paths = TlonPaths(home=tmp_path)
    store = ProgramStore(paths.agent_program_database_file("a") if disk else None, build_id="old")
    journal = SessionJournal("a", paths, program_store=store)
    await journal.append("legacy", "session/start", trigger_content="old", context=[])
    await journal.append("legacy", "session/end", reason="done")
    _entry(store, "early")
    _entry(store, "late")
    _commit(store, "early", "_model-request", {"content": "answer", "tool_calls": []})
    _commit(store, "early", "file-read", {"content": "read bytes"})
    store.complete_entry("late", {"value": "pure"})
    watermark = datetime.now(UTC)
    store.complete_entry("early", {"value": "done"})
    _entry(store, "failed")
    store.fail("failed", "broken")
    assert await journal.list_completed() == [
        "legacy",
        "program:late",
        "program:early",
        "program:failed",
    ]
    assert await journal.list_completed(watermark) == ["program:early", "program:failed"]
    assert await journal.count_completed() == 4
    assert await journal.latest_completed() == "program:failed"
    assert await journal.find_unfinished() == []
    events = await journal.read_events("program:early")
    assert events == await journal.read_events("program:early")
    projection = build_session_projection(events, "program:early")
    assert projection.trigger_content == "hello"
    assert projection.llm_responses == ["answer"]
    assert "read bytes" in projection.tool_results[0]
    assert projection.end_reason == "completed"
    assert (await journal.derive_context("program:early"))[0]["content"] == "question"
    assert await journal.end_reason("program:failed") == "failed"
    with pytest.raises(ValueError, match="cannot be resumed"):
        await journal.resume("program:early")
    if disk:
        # Re-open with another build: history reading must not authorize recovery.
        recovered = SessionJournal("a", paths)
        assert await recovered.read_events("program:early") == events
        assert await recovered.count_completed() == 4


async def test_undated_existing_entries_and_safe_exports(tmp_path: Path) -> None:
    paths = TlonPaths(home=tmp_path)
    store = ProgramStore(paths.agent_program_database_file("a"), build_id="test")
    _entry(store, "../../unsafe")
    store.complete_entry("../../unsafe", None)
    _entry(store, "pending")
    assert store.database is not None
    with sqlite3.connect(store.database) as connection:
        connection.execute("DELETE FROM entry_history_metadata WHERE run_id=?", ("../../unsafe",))
    journal = SessionJournal("a", paths)
    assert await journal.list_completed(datetime.now(UTC)) == ["program:../../unsafe"]
    assert await journal.latest_completed() == "program:../../unsafe"
    await journal.export_jsonl(tmp_path / "exports")
    files = list((tmp_path / "exports").iterdir())
    assert len(files) == 2
    assert all(file.parent == tmp_path / "exports" for file in files)
    assert any("%2F" in file.name for file in files)
    assert not (tmp_path / "unsafe.jsonl").exists()
    pending = await journal.read_events("program:pending")
    assert len(pending) == 1 and pending[0]["type"] == "session/start"
    assert await journal.find_unfinished() == []


async def test_empty_reader_does_not_create_program_authority(tmp_path: Path) -> None:
    paths = TlonPaths(home=tmp_path)
    journal = SessionJournal("a", paths)
    assert await journal.list_completed() == []
    assert await journal.read_events("program:missing") == []
    assert not paths.agent_program_database_file("a").exists()


async def test_partial_failure_keeps_committed_and_pending_facts(tmp_path: Path) -> None:
    paths = TlonPaths(home=tmp_path)
    store = ProgramStore(paths.agent_program_database_file("a"), build_id="test")
    _entry(store, "interrupted")
    _commit(store, "interrupted", "_model-request", {"content": None, "tool_calls": []})
    # The public model operation is another receipt of the same request.
    _commit(store, "interrupted", "model-call", {"content": "must not duplicate"})
    pending = Invocation("unresolved", "interrupted", ("pending",), "shell", "{}", "test", "test")
    store.begin_invocation(pending)
    store.fail("interrupted", "unsafe invocation outcome unknown")
    journal = SessionJournal("a", paths)
    events = await journal.read_events("program:interrupted")
    assert events[0]["responsibility"] == ["mail-1"]
    assert events[0]["source_mail_id"] == "mail-1"
    assert len([e for e in events if e["type"] == "llm/response"]) == 1
    assert next(e for e in events if e["type"] == "llm/response")["response"]["content"] is None
    unresolved = next(e for e in events if e["type"] == "program/invocation-pending")
    assert unresolved["outcome"] == "unknown"
    assert unresolved["invocation"]["name"] == "shell"
    assert events[-1]["error"] == "unsafe invocation outcome unknown"
    projection = build_session_projection(events, "program:interrupted")
    assert "未决调用：shell unresolved" in projection.errors
    assert "unsafe invocation outcome unknown" in projection.errors
    from tlon.memory.session_digest import render_session_digest

    digest = render_session_digest(events)
    assert "未决调用" in digest and "unsafe invocation outcome unknown" in digest
    assert await journal.read_events("program:interrupted") == events
    with pytest.raises(ValueError, match="reserved"):
        await journal.append("program:interrupted", "session/start")


async def test_old_authority_without_clock_table_is_read_only(tmp_path: Path) -> None:
    paths = TlonPaths(home=tmp_path)
    store = ProgramStore(paths.agent_program_database_file("a"), build_id="test")
    _entry(store, "old")
    store.complete_entry("old", None)
    assert store.database is not None
    with sqlite3.connect(store.database) as connection:
        connection.execute("DROP TABLE entry_history_metadata")
    before = store.database.read_bytes()
    store.database.chmod(0o444)
    try:
        reader = SessionJournal("a", paths)
        assert await reader.list_completed(datetime.now(UTC)) == ["program:old"]
        events = await reader.read_events("program:old")
        assert events[0]["ts"] is None and events[-1]["ts"] is None
        assert store.database.read_bytes() == before
        with sqlite3.connect(store.database) as connection:
            assert (
                connection.execute(
                    "SELECT name FROM sqlite_master WHERE name='entry_history_metadata'"
                ).fetchone()
                is None
            )
    finally:
        store.database.chmod(0o644)
