"""Legacy history remains readable and exportable after engine removal."""

import json
from datetime import UTC, datetime
from pathlib import Path

from tests.history_helpers import append_history
from tlon.agents.session_journal import SessionJournal
from tlon.agents.session_store import SessionStore
from tlon.base.paths import TlonPaths


async def test_completed_and_unfinished_legacy_history_remains_readonly(tmp_path: Path) -> None:
    paths = TlonPaths(tmp_path)
    journal = SessionJournal("a", paths)
    await append_history(journal, "done", "session/start", trigger_content="archived", context=[])
    await append_history(journal, "done", "session/end", reason="complete")
    await append_history(journal, "pending", "session/start", trigger_content="not resumed")
    database = paths.agent_sessions_database_file("a")
    original = database.read_bytes()
    assert await journal.list_completed() == ["done"]
    assert await journal.count_completed() == 1
    assert await journal.latest_completed() == "done"
    assert await journal.end_reason("done") == "complete"
    assert await journal.end_reason("pending") is None
    assert (await journal.read_events("pending"))[0]["trigger_content"] == "not resumed"
    assert SessionStore(database).unfinished_ids() == ["pending"]
    assert database.read_bytes() == original
    assert not hasattr(journal, "resume") and not hasattr(journal, "append")


async def test_context_projection_compaction_and_jsonl_export(tmp_path: Path) -> None:
    paths = TlonPaths(tmp_path)
    journal = SessionJournal("a", paths)
    await append_history(
        journal, "a/b", "session/start", context=[{"role": "user", "content": "old"}]
    )
    await append_history(
        journal, "a/b", "context/append", messages=[{"role": "assistant", "content": "reply"}]
    )
    assert len(await journal.derive_context("a/b")) == 2
    await append_history(
        journal, "a/b", "context/compact", context=[{"role": "user", "content": "summary"}]
    )
    await append_history(
        journal, "a/b", "context/append", messages=[{"role": "user", "content": "new"}]
    )
    await append_history(journal, "a/b", "session/end", reason="complete")
    assert await journal.derive_context("a/b") == [
        {"role": "user", "content": "summary"},
        {"role": "user", "content": "new"},
    ]
    output = tmp_path / "export"
    await journal.export_jsonl(output)
    exported = [json.loads(line) for line in (output / "a%2Fb.jsonl").read_text().splitlines()]
    assert exported == await journal.read_events("a/b")
    assert await journal.read_events("missing") == []


async def test_legacy_completion_metadata_preserves_future_watermark_filter(tmp_path: Path) -> None:
    journal = SessionJournal("a", TlonPaths(tmp_path))
    await append_history(journal, "done", "session/start", context=[])
    await append_history(journal, "done", "session/end", reason="complete")
    assert await journal.list_completed(datetime(2200, 1, 1, tzinfo=UTC)) == []
