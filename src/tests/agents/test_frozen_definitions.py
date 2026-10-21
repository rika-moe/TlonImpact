"""Root-scoped definition snapshots retain source without weakening host checks."""

from pathlib import Path

import pytest

from tlon.agents.program_store import ProgramStore
from tlon.operations.schema import RecoveryMismatchError


@pytest.mark.parametrize("disk", [False, True])
def test_definition_snapshot_is_create_once_and_detached(tmp_path: Path, disk: bool) -> None:
    database = tmp_path / "programs.sqlite3" if disk else None
    store = ProgramStore(database, build_id="host")
    source = {"summarize": {"source": "old", "dependency_ids": {"read": "id"}}}
    first = store.freeze_definitions("root", source)
    first["summarize"]["source"] = "mutated projection"
    source["summarize"]["source"] = "mutated caller"
    assert store.freeze_definitions("root", {"summarize": {"source": "new"}}) == {
        "summarize": {"source": "old", "dependency_ids": {"read": "id"}}
    }
    authority = ProgramStore(database, build_id="host") if disk else store
    frozen = authority.load_definitions("root")
    assert frozen is not None
    assert frozen["summarize"]["source"] == "old"
    assert authority.load_definitions("another") is None
    assert authority.freeze_definitions("empty", {}) == {}
    assert authority.freeze_definitions("empty", source) == {}


def test_snapshot_retains_host_build_rejection(tmp_path: Path) -> None:
    database = tmp_path / "programs.sqlite3"
    ProgramStore(database, build_id="old").freeze_definitions("root", {})
    changed = ProgramStore(database, build_id="new")
    with pytest.raises(RecoveryMismatchError, match="Runtime build changed"):
        changed.load_definitions("root")
    with pytest.raises(RecoveryMismatchError, match="Runtime build changed"):
        changed.freeze_definitions("root", {})
    assert changed.freeze_definitions("new-root", {}) == {}


async def test_member_restores_deleted_dependency_and_blocks_invalid_new_behavior(
    tmp_path: Path,
) -> None:
    import asyncio
    import shutil
    from typing import Any
    from unittest.mock import AsyncMock

    from tests.agents.test_member_runtime import _event, _mail, _member, _spec
    from tlon.agents.capabilities.staging import approve_capability, write_staging_capability
    from tlon.base.paths import TlonPaths

    paths = TlonPaths(home=tmp_path)
    source = """{:version 2 :name "send-pair" :description "two messages"
    :input-schema {:type "object" :properties {} :required [] :additionalProperties false}
    :output-schema true
    :impl (fn [input]
      (mail-send {:to ["sink@tlon.internal"] :content "first"})
      (mail-send {:to ["sink@tlon.internal"] :content "second"}))}"""
    write_staging_capability(source, tlon_paths=paths)
    approve_capability("send-pair", paths)
    spec = _spec("(send-pair {})")
    original = _member(tmp_path, spec)
    send = original._post_office.send

    async def interrupted(**kwargs: Any) -> Any:
        if kwargs["content"] == "second":
            raise asyncio.CancelledError()
        return await send(**kwargs)

    original._post_office.send = AsyncMock(side_effect=interrupted)
    event = _event(_mail())
    with pytest.raises(asyncio.CancelledError):
        await original._run_rule_body(spec.rules[0], event)
    unrelated = """{:version 2 :name "other-value" :description "unrelated"
    :input-schema {:type "object" :properties {} :required [] :additionalProperties false}
    :output-schema {:type "integer"} :impl (fn [input] 7)}"""
    write_staging_capability(unrelated, tlon_paths=paths)
    approve_capability("other-value", paths)
    pending = original._program_store.pending()[0]
    assert set(original._program_store.load_definitions(pending.run_id) or {}) == {"send-pair"}
    shutil.rmtree(paths.capabilities_dir() / "send-pair")
    behavior = paths.agent_behavior_file("member")
    behavior.parent.mkdir(parents=True, exist_ok=True)
    behavior.write_text(spec.to_edn(), encoding="utf-8")
    # The registry must admit activation for restoration before validating
    # current behavior; otherwise deletion would prevent reaching the snapshot.
    from tlon.agents.agent_registry import AgentRegistry
    from tlon.agents.vocabulary import make_member_builtins

    registry = AgentRegistry.__new__(AgentRegistry)
    registry._paths = paths
    loaded = registry._load_activation_spec("member", make_member_builtins())
    assert loaded.rules == spec.rules
    restored = _member(tmp_path, loaded)
    assert restored._program_store.build_id == original._program_store.build_id
    assert restored.get_diagnostic_error() is not None
    restored._post_office.send = AsyncMock(wraps=restored._post_office.send)
    await restored._restore_programs()
    assert restored._program_store.pending() == []
    assert [call.kwargs["content"] for call in restored._post_office.send.await_args_list] == [
        "second"
    ]
    # Invalid current source is not admitted just because an old root recovered.
    await restored._on_event(_event(_mail()))
    assert restored._queue.empty()
    with pytest.raises(ValueError, match="required operation"):
        _member(tmp_path, spec)


async def test_session_dispatch_recovers_frozen_tool_after_library_deletion(tmp_path: Path) -> None:
    import asyncio
    import shutil
    from typing import Any
    from unittest.mock import AsyncMock, MagicMock

    from tests.agents.test_member_runtime import RECIPE, _member, _spec
    from tlon.agents.capabilities.staging import approve_capability, write_staging_capability
    from tlon.agents.member_runtime import MemberRuntime, MemberServices
    from tlon.agents.member_spec import SessionRecipe
    from tlon.agents.tool_executor import ToolExecutor
    from tlon.base.config import AgentConfig, LLMConfig
    from tlon.base.execution import MemberWriteGate
    from tlon.base.llm_client import ToolCall
    from tlon.base.paths import TlonPaths
    from tlon.memory.models import MemoryConfig
    from tlosure.edn import loads

    paths = TlonPaths(home=tmp_path)
    source = """{:version 2 :name "notify-pair" :description "two messages"
    :input-schema {:type "object" :properties {} :required [] :additionalProperties false}
    :output-schema true
    :impl (fn [input]
      (mail-send {:to ["sink@tlon.internal"] :content "first"})
      (mail-send {:to ["sink@tlon.internal"] :content "second"}))}"""
    write_staging_capability(source, tlon_paths=paths)
    approve_capability("notify-pair", paths)
    shell = _member(tmp_path, _spec('(state-get {:key "ready"})'))

    def runtime() -> tuple[MemberRuntime, MemberServices]:
        llm = MagicMock()
        llm.config = LLMConfig(model_name="openai/gpt-4o-mini")
        memory = MagicMock()
        memory.config = MemoryConfig()
        caps = MemberServices(
            config=AgentConfig(id="member", name="Member"),
            llm_client=llm,
            memory=memory,
            task_manager=MagicMock(),
            tool_executor=ToolExecutor(),
            tlon_paths=paths,
            write_gate=MemberWriteGate(),
        )
        from tlon.agents.vocabulary import make_member_builtins

        return MemberRuntime(
            member_id="member",
            name="Member",
            event_bus=shell._event_bus,
            spec=shell.spec,
            builtins=make_member_builtins(),
            mailbox=shell._mailbox,
            post_office=shell._post_office,
            services=caps,
        ), caps

    member, caps = runtime()
    recipe = SessionRecipe.from_edn(loads(RECIPE))
    driver = member._assemble_session(caps, recipe, root_id="frozen-session")
    call = ToolCall(id="pair", function_name="notify-pair", arguments={})
    build_id = await member._verify_session_root("frozen-session")
    metadata = driver._operation_recovery("frozen-session", call, 0, build_id)
    send = member._post_office.send

    async def interrupted(**kwargs: Any) -> Any:
        if kwargs["content"] == "second":
            raise asyncio.CancelledError()
        return await send(**kwargs)

    member._post_office.send = AsyncMock(side_effect=interrupted)
    with pytest.raises(asyncio.CancelledError):
        await driver.execute_one_tool(
            "frozen-session",
            call,
            driver._operation_context("frozen-session", call, 0),
            journal_result=False,
        )
    shutil.rmtree(paths.capabilities_dir() / "notify-pair")
    restarted, new_caps = runtime()
    assert not new_caps.tool_executor.has_tool("notify-pair")
    resumed = restarted._assemble_session(new_caps, recipe, root_id="frozen-session")
    restarted._post_office.send = AsyncMock(wraps=send)
    result = await resumed.resolve_dispatch("frozen-session", call, metadata)
    assert result.success
    assert [item.kwargs["content"] for item in restarted._post_office.send.await_args_list] == [
        "second"
    ]


@pytest.mark.parametrize("disk", [False, True])
def test_program_and_dependencies_are_admitted_atomically(tmp_path: Path, disk: bool) -> None:
    store = ProgramStore(tmp_path / "programs.sqlite3" if disk else None, build_id="host")
    first = {"pair": {"source": "original", "dependency_ids": {"leaf": "id"}}}
    records = store.ensure_many(
        [("one", "rule", "input"), ("two", "other-rule", "other-input")],
        definitions={"one": first, "two": {}},
    )
    assert len(records) == 2
    assert store.load_definitions("one") == first
    assert store.load_definitions("two") == {}
    store.ensure("one", "changed-rule", "changed-input", definitions={"replacement": {}})
    assert store.load_definitions("one") == first
    assert store.get_run("one") == records[0]


def test_failed_program_admission_does_not_leave_dependency_snapshot(tmp_path: Path) -> None:
    import sqlite3

    store = ProgramStore(tmp_path / "programs.sqlite3", build_id="host")
    with store._connection() as connection:
        connection.execute(
            "CREATE TRIGGER reject_admission BEFORE INSERT ON runs "
            "BEGIN SELECT RAISE(FAIL, 'crash during admission'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="crash during admission"):
        store.ensure("root", "rule", "input", definitions={"pair": {"source": "frozen"}})
    assert store.get_run("root") is None
    assert store.load_definitions("root") is None


def test_new_behavior_rejects_unassembled_required_service(tmp_path: Path) -> None:
    from tests.agents.test_member_runtime import _member, _spec

    with pytest.raises(ValueError, match="service not assembled.*file-read"):
        _member(tmp_path, _spec('(file-read {:path "notes.txt"})'))


def test_new_behavior_rejects_transitive_unassembled_service(tmp_path: Path) -> None:
    from tests.agents.test_member_runtime import _member, _spec
    from tlon.agents.capabilities.staging import approve_capability, write_staging_capability
    from tlon.base.paths import TlonPaths

    paths = TlonPaths(home=tmp_path)
    source = """{:version 2 :name "read-notes" :description "read explicit notes"
    :input-schema {:type "object" :properties {} :required [] :additionalProperties false}
    :output-schema true
    :impl (fn [input] (file-read {:path "notes.txt"}))}"""
    write_staging_capability(source, tlon_paths=paths)
    approve_capability("read-notes", paths)
    with pytest.raises(ValueError, match="rejected composite definitions.*file-read"):
        _member(tmp_path, _spec("(read-notes {})"))
