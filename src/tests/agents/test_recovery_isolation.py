"""Unrecoverable roots are preserved and isolated while new work progresses."""

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from tests.agents.test_member_runtime import RECIPE, _drain, _event, _mail, _member, _spec
from tests.agents.test_session_execution import provider_response, real_driver, scoped_session
from tests.agents.test_session_journal import (
    _CompletingDriver,
    _make_driver,
    _make_scheduler,
    _session,
    _tc,
)
from tlon.agents.recovery import RecoveryBlockedError
from tlon.agents.session import AgentSession
from tlon.agents.session_journal import SessionJournal
from tlon.base.execution import UnknownModelOutcomeError, bind_execution
from tlon.base.paths import TlonPaths
from tlon.operations.adapters import OperationTool
from tlon.operations.core import OperationExecutor, OperationRegistry
from tlon.operations.dsl import DSLAdapter
from tlon.operations.model import make_model_operations
from tlon.operations.schema import RecoveryMismatchError, UnknownOutcomeError
from tlosure.edn import dumps


async def test_mismatched_program_quarantined_across_restart_and_new_mail_runs(
    tmp_path: Path,
) -> None:
    spec = _spec('(state-put {:key "result" :value "new"})')
    before = _member(tmp_path, spec)
    event = _event(_mail())
    record = before._program_store.ensure(
        *before._program_record_data(spec.rules[0], event, event.payload)
    )
    original = before._program_store.get_run(record.run_id)
    # A deployment changes runtime identity, leaving frozen source/input intact.
    before._program_store.build_id = "previous-runtime"
    with before._program_store._connection() as connection:
        connection.execute("UPDATE roots SET build_id=?", ("previous-runtime",))
    restarted = _member(tmp_path, spec)
    await restarted.initialize()
    try:
        await _drain(restarted)
        await restarted._on_event(_event(_mail()))
        await _drain(restarted)
        assert restarted._program_task is not None and not restarted._program_task.done()
        blocked = restarted._program_store.get_run(record.run_id)
        assert blocked is not None and not blocked.completed
        assert blocked.error and blocked.error.startswith("recovery_blocked:RecoveryMismatchError:")
        assert original is not None and (blocked.rule_edn, blocked.input_edn) == (
            original.rule_edn,
            original.input_edn,
        )
        assert restarted._program_store.state_get("result") == "new"
        assert restarted._program_store.pending() == []
    finally:
        await restarted.shutdown()
    again = _member(tmp_path, spec)
    await again._restore_programs()
    assert again._program_store.get_run(record.run_id) == blocked
    with pytest.raises(RecoveryMismatchError):
        again._program_store.ensure_root(record.run_id)
    # Re-delivery inspects the failed lifecycle without replaying old writes.
    await again._dispatch_event(event)
    assert again._program_store.get_run(record.run_id) == blocked


async def test_program_model_unknown_is_not_retried_or_allowed_to_continue(tmp_path: Path) -> None:
    spec = _spec(
        '(model-call {:prompt "work" :max_tokens 10}) (state-put {:key "old" :value true})'
    )
    # This fixture injects its model service after constructing the bare host.
    member = _member(tmp_path, _spec('(state-get {:key "fixture"})'))
    member._spec = spec
    provider = AsyncMock(side_effect=UnknownModelOutcomeError("response unknown"))
    definitions = make_model_operations(
        type("Client", (), {"chat": provider})(), AsyncMock(return_value="persona")
    )
    old = member._operations._executor
    member._operations = DSLAdapter(
        OperationExecutor(
            OperationRegistry(
                [
                    operation
                    for operation in old.registry.definitions()
                    if operation.name != "model-call"
                ]
                + list(definitions)
            ),
            old.context,
            member._program_store,
        )
    )
    member._operation_executor = member._operations._executor
    member._builtins.update(member._operations.builtins())
    with pytest.raises(UnknownOutcomeError):
        await member._run_rule_body(spec.rules[0], _event(_mail()))
    provider.assert_awaited_once()
    assert member._program_store.pending() == []
    with member._program_store._connection() as connection:
        row = connection.execute("SELECT completed,error FROM runs").fetchone()
    assert row[0] == 0 and row[1].startswith("recovery_blocked:")
    restored = _member(tmp_path, _spec('(state-get {:key "old"})'))
    await restored._restore_programs()
    assert restored._program_store.state_get("old") is None
    provider.assert_awaited_once()


async def test_startup_session_mismatch_isolates_parent_and_continues_healthy_root(
    tmp_path: Path,
) -> None:
    member = _member(tmp_path, _spec("(str 1)"))
    run = member._program_store.ensure(*member._program_record_data(member.spec.rules[0], None, {}))
    paths = TlonPaths(home=tmp_path)
    journal = SessionJournal("agent1", paths, verify_root=AsyncMock(return_value="new-build"))
    driver = _CompletingDriver()
    scheduler = _make_scheduler(journal, loop_driver=driver)
    scheduler._resume_invocation = member._resume_invocation
    scheduler._block_invocation = member._block_invocation
    from tlon.agents.member_spec import SessionRecipe
    from tlosure.edn import loads

    recipe = SessionRecipe.from_edn(loads(RECIPE))
    for sid, build in [("a-bad", "old-build"), ("b-good", "new-build")]:
        await journal.append(
            sid,
            "session/start",
            context=[],
            build_id=build,
            recipe=RECIPE,
            invocation={
                "program_effect": {
                    "run_id": run.run_id,
                    "index": 0,
                    "name": "run-loop",
                    "args_edn": "nil",
                }
            }
            if sid == "a-bad"
            else {},
        )
    scheduler.start()
    try:
        await asyncio.wait_for(scheduler.wait_recovered(), 2)
        assert scheduler._worker_task is not None and not scheduler._worker_task.done()
        assert [session.id for session in driver.sessions] == ["b-good"]
        assert await journal.end_reason("a-bad") == "recovery_blocked"
        assert await journal.list_completed() == ["b-good"]
        assert await journal.count_completed() == 1
        assert await journal.latest_completed() == "b-good"
        assert member._program_store.get_run(run.run_id).error.startswith("recovery_blocked:")
        with pytest.raises(RecoveryBlockedError):
            await journal.resume("a-bad")
        await scheduler.run_invocation(recipe, "fresh", [], None, {})
        assert len(driver.sessions) == 2
    finally:
        await scheduler.stop()
    again = _make_scheduler(journal, loop_driver=driver)
    again.start()
    try:
        await asyncio.wait_for(again.wait_recovered(), 2)
        assert len(driver.sessions) == 2
    finally:
        await again.stop()


async def test_recovered_model_unknown_preserves_reservation_and_scheduler_liveness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = AsyncMock(side_effect=OSError("unknown provider response"))
    monkeypatch.setattr("litellm.acompletion", provider)
    driver, journal, store = real_driver(tmp_path, monkeypatch)
    scope = await scoped_session(store, "a-unknown", 100)
    initial = _session("a-unknown")
    initial.recipe_source = dumps(driver._loop.to_edn_data())
    with bind_execution(scope), pytest.raises(UnknownModelOutcomeError):
        await driver.step_session(initial)
    frozen = store.budget_usage("a-unknown")
    assert frozen["remaining"] == 0
    restored, journal, store = real_driver(tmp_path, monkeypatch)
    scheduler = _make_scheduler(journal, loop_driver=restored)
    scheduler._execution_factory = lambda sid, recipe: scoped_session(store, sid, 100)
    scheduler.start()
    try:
        await asyncio.wait_for(scheduler.wait_recovered(), 2)
        assert await journal.end_reason("a-unknown") == "recovery_blocked"
        assert not scheduler._worker_task.done()
        assert store.budget_usage("a-unknown") == frozen
        assert provider.await_count == 1
        provider.side_effect = None
        provider.return_value = provider_response()
        await scheduler.run_invocation(restored._loop, "new", [], None, {})
        assert provider.await_count > 1
    finally:
        await scheduler.stop()
    replay = _make_scheduler(journal, loop_driver=restored)
    replay._execution_factory = scheduler._execution_factory
    count = provider.await_count
    replay.start()
    try:
        await asyncio.wait_for(replay.wait_recovered(), 2)
        assert provider.await_count == count
        assert store.budget_usage("a-unknown") == frozen
    finally:
        await replay.stop()


async def test_failed_parent_prevents_completed_session_continuation(tmp_path: Path) -> None:
    member = _member(tmp_path, _spec("(str 1)"))
    run = member._program_store.ensure(*member._program_record_data(member.spec.rules[0], None, {}))
    member._program_store.fail(run.run_id, "recovery_blocked:original model unknown")
    session = AgentSession(
        "completed",
        "work",
        invocation={
            "program_effect": {
                "run_id": run.run_id,
                "index": 0,
                "name": "run-loop",
                "args_edn": "nil",
            }
        },
    )
    with pytest.raises(RecoveryBlockedError):
        await member._resume_invocation(session)
    assert member._program_store.get_effect(run.run_id, 0, "run-loop", "nil") == (False, None)


async def test_ambiguous_same_batch_tool_ids_are_never_recovered(tmp_path: Path) -> None:
    journal = SessionJournal("a", TlonPaths(home=tmp_path))
    await journal.append("bad", "session/start", context=[])
    await journal.append(
        "bad", "tool/dispatch", tool_calls=[_tc("same", "x").to_dict(), _tc("same", "y").to_dict()]
    )
    journal.resolve_dispatch = AsyncMock()
    with pytest.raises(RecoveryMismatchError):
        await journal.resume("bad")
    journal.resolve_dispatch.assert_not_awaited()


async def test_parent_block_crash_window_stops_dispatch_recovery_before_effect(
    tmp_path: Path,
) -> None:
    member = _member(tmp_path, _spec("(str 1)"))
    run = member._program_store.ensure(*member._program_record_data(member.spec.rules[0], None, {}))
    member._program_store.fail(run.run_id, "recovery_blocked:model uncertainty")
    resolver = AsyncMock()
    journal = SessionJournal(
        "agent1",
        TlonPaths(home=tmp_path),
        verify_invocation=member._verify_invocation,
        resolve_dispatch=resolver,
    )
    await journal.append(
        "crash-window",
        "session/start",
        context=[],
        recipe=RECIPE,
        invocation={
            "program_effect": {
                "run_id": run.run_id,
                "index": 0,
                "name": "run-loop",
                "args_edn": "nil",
            }
        },
    )
    await journal.append("crash-window", "tool/dispatch", tool_calls=[_tc("orphan").to_dict()])
    scheduler = _make_scheduler(journal, loop_driver=_CompletingDriver())
    scheduler._block_invocation = member._block_invocation
    scheduler.start()
    try:
        await asyncio.wait_for(scheduler.wait_recovered(), 2)
        resolver.assert_not_awaited()
        assert await journal.end_reason("crash-window") == "recovery_blocked"
        assert not any(
            event["type"] == "tool/result" for event in await journal.read_events("crash-window")
        )
        assert not scheduler._worker_task.done()
    finally:
        await scheduler.stop()


@pytest.mark.parametrize("ended", [False, True], ids=["unfinished", "ended-before-continuation"])
async def test_safe_mail_unknown_waits_for_receipt_without_killing_startup(
    tmp_path: Path, ended: bool
) -> None:
    from tlon.agents.trigger import AgentIdentity
    from tlon.base.execution import ExecutionScope
    from tlon.organization.mail.mailbox import Mailbox

    member = _member(tmp_path, _spec("(str 1)"))
    resolving_driver, journal, _, _ = _make_driver(tmp_path, [])
    resolving_driver._identity = AgentIdentity(
        id="member", name="Member", address="member@tlon.internal"
    )
    resolving_driver._tool_executor.register_tool(
        OperationTool(member._operations._executor, "mail-send")
    )
    journal._verify_root = member._verify_session_root
    journal.resolve_dispatch = resolving_driver.resolve_dispatch
    record = member._program_store.ensure(
        *member._program_record_data(member.spec.rules[0], None, {})
    )
    effect = {"run_id": record.run_id, "index": 0, "name": "run-loop", "args_edn": "nil"}
    sid = "pending-mail"
    tc = _tc("mail", "mail-send", {"to": ["sink@tlon.internal"], "content": "one"})
    build = await journal.verify_root(sid)
    await journal.append(
        sid,
        "session/start",
        recipe=RECIPE,
        context=[],
        build_id=build,
        invocation={"program_effect": effect},
    )
    await journal.append(
        sid,
        "tool/dispatch",
        tool_calls=[tc.to_dict()],
        operation_recovery={tc.id: resolving_driver._operation_recovery(sid, tc, 0, build)},
    )
    if ended:
        await journal.append(sid, "session/end", reason="timeout")
    first_failure = asyncio.Event()
    released = False
    send = member._post_office.send

    async def temporary(**kwargs: Any) -> Any:
        if not released:
            first_failure.set()
            raise OSError("mail receipt temporarily unavailable")
        return await send(**kwargs)

    member._post_office.send = AsyncMock(side_effect=temporary)
    driver = _CompletingDriver()
    scheduler = _make_scheduler(journal, loop_driver=driver)

    async def scope(sid: str, recipe: Any) -> ExecutionScope:
        result = ExecutionScope(member._program_store, sid, 100, "total")
        await result.initialize()
        return result

    scheduler._execution_factory = scope
    scheduler._resume_invocation = member._resume_invocation
    scheduler._needs_continuation = member._needs_continuation
    scheduler.start()
    try:
        await asyncio.wait_for(first_failure.wait(), 2)
        assert not scheduler.recovery_ready.is_set()
        assert not scheduler._worker_task.done()
        assert await journal.end_reason(sid) == ("timeout" if ended else None)
        assert not any(event["type"] == "tool/result" for event in await journal.read_events(sid))
        released = True
        await asyncio.wait_for(scheduler.wait_recovered(), 2)
        assert [session.id for session in driver.sessions] == ([] if ended else [sid])
        assert await journal.end_reason(sid) == ("timeout" if ended else "complete")
        assert member._program_store.get_effect(record.run_id, 0, "run-loop", "nil") == (
            True,
            "nil",
        )
        assert len(await Mailbox(member._post_office.store.database, "sink").inbox()) == 1
        assert (
            len(
                [
                    event
                    for event in await journal.read_events(sid)
                    if event["type"] == "tool/result"
                ]
            )
            == 1
        )
    finally:
        await scheduler.stop()


async def test_unsafe_non_model_unknown_blocks_root_and_allows_new_work(tmp_path: Path) -> None:
    journal = SessionJournal("a", TlonPaths(home=tmp_path))
    await journal.append("unsafe-host", "session/start", recipe=RECIPE, context=[])

    class UnsafeDriver(_CompletingDriver):
        async def step_session(self, session: AgentSession) -> None:
            if session.id == "unsafe-host":
                raise UnknownOutcomeError("shell submitted without receipt", {"retry_safe": False})
            await super().step_session(session)

    driver = UnsafeDriver()
    scheduler = _make_scheduler(journal, loop_driver=driver)
    scheduler.start()
    try:
        await asyncio.wait_for(scheduler.wait_recovered(), 2)
        assert await journal.end_reason("unsafe-host") == "recovery_blocked"
        assert not scheduler._worker_task.done()
        from tlon.agents.member_spec import SessionRecipe
        from tlosure.edn import loads

        await scheduler.run_invocation(SessionRecipe.from_edn(loads(RECIPE)), "new", [], None, {})
        assert len(driver.sessions) == 1
    finally:
        await scheduler.stop()


@pytest.mark.parametrize(
    "parent_completed", [False, True], ids=["effect-recorded", "parent-completed"]
)
async def test_successful_history_keeps_completion_after_deployment(
    tmp_path: Path, parent_completed: bool
) -> None:
    member = _member(tmp_path, _spec("(str 1)"))
    record = member._program_store.ensure(
        *member._program_record_data(member.spec.rules[0], None, {})
    )
    effect = {"run_id": record.run_id, "index": 0, "name": "run-loop", "args_edn": "nil"}
    if parent_completed:
        member._program_store.complete(record.run_id)
    else:
        member._program_store.record_effect(record.run_id, 0, "run-loop", "nil", "nil")
    journal = SessionJournal(
        "a",
        TlonPaths(home=tmp_path),
        verify_root=AsyncMock(return_value="new-build"),
        verify_invocation=member._verify_invocation,
    )
    await journal.append(
        "success",
        "session/start",
        recipe=RECIPE,
        context=[],
        build_id="old-build",
        invocation={"program_effect": effect},
    )
    await journal.append("success", "session/end", reason="complete")
    original = await journal.read_events("success")
    scheduler = _make_scheduler(journal)
    scheduler._resume_invocation = AsyncMock(wraps=member._resume_invocation)
    scheduler._needs_continuation = member._needs_continuation
    scheduler.start()
    try:
        await asyncio.wait_for(scheduler.wait_recovered(), 2)
        scheduler._resume_invocation.assert_not_awaited()
        journal._verify_root.assert_not_awaited()
        assert await journal.read_events("success") == original
        assert await journal.end_reason("success") == "complete"
        assert await journal.count_completed() == 1
    finally:
        await scheduler.stop()


@pytest.mark.parametrize("mismatch", ["definition", "missing-metadata"])
async def test_frozen_dispatch_mismatch_quarantined_before_business(
    tmp_path: Path, mismatch: str
) -> None:
    business = AsyncMock()
    resolving, journal, _, _ = _make_driver(tmp_path, [], tool_execute=business)
    journal._verify_root = AsyncMock(return_value="frozen")
    journal.resolve_dispatch = resolving.resolve_dispatch
    tc = _tc("call")
    metadata = resolving._operation_recovery("a-bad", tc, 0, "frozen")
    if mismatch == "definition":
        metadata["definition_id"] = "other-definition"
    else:
        metadata.pop("call_path")
    await journal.append("a-bad", "session/start", context=[], recipe=RECIPE, build_id="frozen")
    await journal.append(
        "a-bad", "tool/dispatch", tool_calls=[tc.to_dict()], operation_recovery={tc.id: metadata}
    )
    await journal.append("b-good", "session/start", context=[], recipe=RECIPE, build_id="frozen")
    healthy = _CompletingDriver()
    scheduler = _make_scheduler(journal, loop_driver=healthy)
    scheduler.start()
    try:
        await asyncio.wait_for(scheduler.wait_recovered(), 2)
        business.assert_not_awaited()
        assert await journal.end_reason("a-bad") == "recovery_blocked"
        assert [session.id for session in healthy.sessions] == ["b-good"]
        assert not scheduler._worker_task.done()
    finally:
        await scheduler.stop()


async def test_completed_continuation_effect_conflict_isolated_before_replay(
    tmp_path: Path,
) -> None:
    member = _member(tmp_path, _spec("(str 1)"))
    record = member._program_store.ensure(
        *member._program_record_data(member.spec.rules[0], None, {})
    )
    member._program_store.record_effect(record.run_id, 0, "run-loop", "original", "nil")
    journal = SessionJournal("a", TlonPaths(home=tmp_path))
    await journal.append(
        "conflict",
        "session/start",
        context=[],
        recipe=RECIPE,
        invocation={
            "program_effect": {
                "run_id": record.run_id,
                "index": 0,
                "name": "run-loop",
                "args_edn": "changed",
            }
        },
    )
    await journal.append("conflict", "session/end", reason="complete")
    scheduler = _make_scheduler(journal, loop_driver=_CompletingDriver())
    scheduler._needs_continuation = member._needs_continuation
    scheduler._resume_invocation = member._resume_invocation
    scheduler._block_invocation = member._block_invocation
    scheduler.start()
    try:
        await asyncio.wait_for(scheduler.wait_recovered(), 2)
        assert await journal.end_reason("conflict") == "recovery_blocked"
        assert not scheduler._worker_task.done()
        assert member._program_store.get_effect(record.run_id, 0, "run-loop", "original") == (
            True,
            "nil",
        )
        assert member._program_store.get_run(record.run_id).error.startswith("recovery_blocked:")
    finally:
        await scheduler.stop()


async def test_missing_parent_does_not_create_frozen_root(tmp_path: Path) -> None:
    member = _member(tmp_path, _spec("(str 1)"))
    invocation = {
        "program_effect": {"run_id": "missing", "index": 0, "name": "run-loop", "args_edn": "nil"}
    }
    with pytest.raises(RecoveryBlockedError, match="parent program is missing"):
        await member._verify_invocation(invocation)
    with member._program_store._connection() as connection:
        assert connection.execute("SELECT * FROM roots WHERE root_id='missing'").fetchall() == []
