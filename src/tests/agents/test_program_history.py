"""Program reading views rebuild exclusively from committed execution facts."""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.history_helpers import append_history
from tlon.agents.program_store import ProgramStore
from tlon.agents.run_journal import RunJournal
from tlon.base.paths import TlonPaths
from tlon.memory.models import build_run_projection
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
async def test_rebuild_run_history_without_execution(tmp_path: Path, disk: bool) -> None:
    paths = TlonPaths(home=tmp_path)
    store = ProgramStore(paths.agent_program_database_file("a") if disk else None, build_id="old")
    journal = RunJournal("a", paths, program_store=store)
    await append_history(journal, "earliest", "run/start", trigger_content="old", context=[])
    await append_history(journal, "earliest", "run/end", reason="done")
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
        "earliest",
        "late",
        "early",
        "failed",
    ]
    assert await journal.list_completed(watermark) == ["early", "failed"]
    assert await journal.count_completed() == 4
    assert await journal.latest_completed() == "failed"
    assert await journal.list_pending() == []
    events = await journal.read_events("early")
    assert events == await journal.read_events("early")
    projection = build_run_projection(events, "early")
    assert projection.trigger_content == "hello"
    assert projection.llm_responses == ["answer"]
    assert "read bytes" in projection.tool_results[0]
    assert projection.end_reason == "completed"
    assert (await journal.derive_context("early"))[0]["content"] == "question"
    assert await journal.end_reason("failed") == "failed"
    assert not hasattr(journal, "resume")
    if disk:
        # Re-open with another build: history reading must not authorize recovery.
        recovered = RunJournal("a", paths)
        assert await recovered.read_events("early") == events
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
    journal = RunJournal("a", paths)
    assert await journal.list_completed(datetime.now(UTC)) == ["../../unsafe"]
    assert await journal.latest_completed() == "../../unsafe"
    await journal.export_jsonl(tmp_path / "exports")
    files = list((tmp_path / "exports").iterdir())
    assert len(files) == 2
    assert all(file.parent == tmp_path / "exports" for file in files)
    assert any("%2F" in file.name for file in files)
    assert not (tmp_path / "unsafe.jsonl").exists()
    pending = await journal.read_events("pending")
    assert len(pending) == 1 and pending[0]["type"] == "run/start"
    assert await journal.list_pending() == ["pending"]


async def test_empty_reader_does_not_create_program_authority(tmp_path: Path) -> None:
    paths = TlonPaths(home=tmp_path)
    journal = RunJournal("a", paths)
    assert await journal.list_completed() == []
    assert await journal.read_events("missing") == []
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
    journal = RunJournal("a", paths)
    events = await journal.read_events("interrupted")
    assert events[0]["responsibility"] == ["mail-1"]
    assert events[0]["source_mail_id"] == "mail-1"
    assert len([e for e in events if e["type"] == "llm/response"]) == 1
    assert next(e for e in events if e["type"] == "llm/response")["response"]["content"] is None
    unresolved = next(e for e in events if e["type"] == "program/invocation-pending")
    assert unresolved["outcome"] == "unknown"
    assert unresolved["invocation"]["name"] == "shell"
    assert events[-1]["error"] == "unsafe invocation outcome unknown"
    projection = build_run_projection(events, "interrupted")
    assert "未决调用：shell unresolved" in projection.errors
    assert "unsafe invocation outcome unknown" in projection.errors
    from tlon.memory.run_digest import render_run_digest

    digest = render_run_digest(events)
    assert "未决调用" in digest and "unsafe invocation outcome unknown" in digest
    assert await journal.read_events("interrupted") == events
    assert not hasattr(journal, "append")


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
        reader = RunJournal("a", paths)
        assert await reader.list_completed(datetime.now(UTC)) == ["old"]
        events = await reader.read_events("old")
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


async def test_legacy_session_archive_is_ignored_and_run_ids_are_literal(tmp_path: Path) -> None:
    paths = TlonPaths(home=tmp_path)
    archive = paths.agent_dir("a") / "sessions.sqlite3"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"unrecognized legacy archive")
    before = archive.read_bytes()
    store = ProgramStore(paths.agent_program_database_file("a"), build_id="test")
    _entry(store, "literal")
    store.complete_entry("literal", None)
    journal = RunJournal("a", paths, program_store=store)
    assert await journal.list_completed() == ["literal"]
    assert await journal.read_events("program:literal") == []
    events = await journal.read_events("literal")
    assert events[0]["type"] == "run/start" and events[0]["run_id"] == "literal"
    assert events[-1]["type"] == "run/end"
    assert not any("session_id" in event for event in events)
    assert archive.read_bytes() == before
