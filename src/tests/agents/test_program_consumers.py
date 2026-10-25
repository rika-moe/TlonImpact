"""The existing reading consumers see module runs without stage execution."""

from pathlib import Path

from tlon.agents.context_builder import _render_thread_recap
from tlon.agents.program_store import ProgramStore
from tlon.agents.run_journal import RunJournal
from tlon.base.paths import TlonPaths
from tlon.operations.core import CallContext
from tlon.operations.runs import make_run_read_operations
from tlosure.edn import dumps


def _completed(paths: TlonPaths) -> None:
    store = ProgramStore(paths.agent_program_database_file("a"))
    store.ensure_entry(
        "mail-1:handler:mail-delivered",
        "{}",
        dumps({"payload": {"mail": {"__tlon_mail__": {"id": "mail-1", "content": "核验部署"}}}}),
        {},
    )
    store.complete_entry("mail-1:handler:mail-delivered", {"answer": "已核验"})


async def test_run_read_and_recap_include_original_program(tmp_path: Path) -> None:
    paths = TlonPaths(home=tmp_path)
    _completed(paths)
    journal = RunJournal("a", paths)
    source = "mail-1:handler:mail-delivered"
    events = await journal.read_events(source)
    recap = _render_thread_recap(events)
    assert source in recap and "核验部署" in recap and "已核验" in recap
    operation = next(op for op in make_run_read_operations(journal, "a") if op.name == "run-read")
    result = await operation.handler(CallContext("a", "root", "read"), {"run_id": source})
    assert result["run_id"] == source
    assert "已核验" in result["digest"]
    assert not hasattr(journal, "find_unfinished")


async def test_arena_export_preserves_source_ids_and_resolvable_evidence(tmp_path: Path) -> None:
    from tlon.arena.report import _load_runs, _summarize_run

    paths = TlonPaths(home=tmp_path)
    _completed(paths)
    store = ProgramStore(paths.agent_program_database_file("a"))
    store.ensure_entry("failed/entry", "{}", "{}", {})
    store.fail("failed/entry", "fuel exhausted")
    archive = tmp_path / "archive"
    await RunJournal("a", paths).export_jsonl(archive / "runs")
    sources = _load_runs(archive)
    assert {item.run_id for item in sources} == {
        "mail-1:handler:mail-delivered",
        "failed/entry",
    }
    for item in sources:
        summary, flags = _summarize_run(item.events, filename=item.filename)
        if item.run_id.endswith("mail-delivered"):
            assert summary["end_reason"] == "completed"
            assert not flags
        for flag in flags:
            assert (archive / flag.evidence.split("#")[0]).is_file()
