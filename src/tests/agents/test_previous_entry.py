"""Previous results are a read-only view of completed entry facts."""

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from tlon.agents.program_store import ProgramStore


def package(entry: str = "work/handle", **changes: Any) -> dict[str, Any]:
    return {
        "entry": entry,
        "modules": {"work": "source version one"},
        "model_contexts": {
            "host": {"system_prompt": "persona", "tools": [{"function": {"name": "mail-send"}}]},
            "config": {"model_name": "openai/test", "temperature": 0.7},
        },
        **changes,
    }


def entry(store: ProgramStore, run_id: str, value: dict[str, Any] | None = None) -> None:
    store.ensure_entry(run_id, "{}", "{}", value or package())


@pytest.fixture(params=[False, True])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> ProgramStore:
    return ProgramStore(tmp_path / "program.sqlite3" if request.param else None, build_id="test")


def test_latest_successful_result_same_entry_and_before_current_start(store: ProgramStore) -> None:
    entry(store, "old")
    store.complete_entry("old", {"messages": [{"role": "user", "content": "old"}]})
    entry(store, "new")
    result = {"messages": [{"role": "assistant", "content": None, "tool_calls": []}], "other": 9}
    store.complete_entry("new", result)
    entry(store, "other", package("other/handle"))
    store.complete_entry("other", {"messages": ["do not inherit"]})
    entry(store, "failed")
    store.fail("failed", "broken")
    entry(store, "late")
    entry(store, "current")
    store.complete_entry("late", {"messages": ["completed after current started"]})
    reading = store.previous_entry("current")
    assert reading["previous"] == {
        "run_id": "new",
        "contract": reading["contract"],
        "result": result,
    }
    reading["previous"]["result"]["messages"].clear()
    assert store.previous_entry("current")["previous"]["result"] == result
    store.complete_entry("current", {"messages": ["self"]})
    assert store.previous_entry("current")["previous"]["run_id"] == "new"


def test_contract_tracks_host_model_tools_but_not_program_sources(store: ProgramStore) -> None:
    entry(store, "first")
    store.complete_entry("first", {"messages": []})
    entry(store, "source-change", package(modules={"work": "new source"}))
    baseline = store.previous_entry("source-change")
    assert baseline["contract"] == baseline["previous"]["contract"]
    for label, slot, key, value in [
        ("model", "config", "model_name", "other/model"),
        ("host", "host", "system_prompt", "changed persona"),
        ("tools", "host", "tools", []),
    ]:
        changed = package()
        changed["model_contexts"][slot][key] = value
        entry(store, label, changed)
        reading = store.previous_entry(label)
        assert reading["contract"] != reading["previous"]["contract"]
        assert reading["previous"]["run_id"] == "first"


def test_no_previous_and_unrecorded_entry(store: ProgramStore) -> None:
    entry(store, "first")
    assert store.previous_entry("first")["previous"] is None
    with pytest.raises(ValueError, match="recorded program entry"):
        store.previous_entry("missing")


def test_disk_restart_reads_only_authority_and_preserves_completion_order(tmp_path: Path) -> None:
    database = tmp_path / "program.sqlite3"
    store = ProgramStore(database, build_id="test")
    for run in ("z", "a"):
        entry(store, run)
    store.complete_entry("z", {"value": "z"})
    store.complete_entry("a", {"value": "a"})
    entry(store, "current")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE entry_history_metadata SET ended_at='2020-01-01T00:00:00+00:00' WHERE run_id IN ('a','z')"
        )
    before = database.read_bytes()
    database.chmod(0o444)
    try:
        restarted = ProgramStore(database, build_id="different")
        # Stable completion tie uses raw run ID, independent of insertion order.
        assert restarted.previous_entry("current")["previous"]["run_id"] == "z"
        assert database.read_bytes() == before
    finally:
        database.chmod(0o644)
