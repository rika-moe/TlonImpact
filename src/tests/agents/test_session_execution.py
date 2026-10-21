"""Real scoped model requests remain stable across journal crash windows."""

import asyncio
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.agents.test_session_journal import _make_driver, _make_scheduler, _session, _tc
from tlon.agents.context_builder import BuiltContext
from tlon.agents.context_pressure import ContextPressure
from tlon.agents.loop_driver import LoopDriver
from tlon.agents.program_store import ProgramStore
from tlon.agents.recovery import RecoveryBlockedError
from tlon.agents.session import AgentSession
from tlon.agents.session_journal import SessionJournal
from tlon.agents.stages.critique import critique_stage
from tlon.agents.stages.outcome import StageOutcome
from tlon.agents.stages.plan import plan_stage
from tlon.agents.stages.services import StageServices
from tlon.agents.stages.think import think_stage
from tlon.base.config import LLMConfig
from tlon.base.execution import ExecutionScope, UnknownModelOutcomeError, bind_execution
from tlon.base.llm_client import LLMClient
from tlon.operations.adapters import OperationTool
from tlon.operations.core import CallContext, Operation, OperationExecutor, OperationRegistry
from tlon.operations.schema import UnknownOutcomeError


def provider_response(content: str = "done") -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content, tool_calls=None, reasoning_content=None),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(
            model_dump=lambda: {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
        ),
    )


def real_driver(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[LoopDriver, SessionJournal, Any]:
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setattr("litellm.token_counter", lambda **kwargs: 1)
    driver, journal, _, _ = _make_driver(tmp_path, [])
    builder = MagicMock()
    builder.build_context = AsyncMock(
        return_value=BuiltContext(messages=[{"role": "user", "content": "hi"}], anchor_len=1)
    )
    builder.get_system_prompt = AsyncMock(return_value="stable persona")
    client = LLMClient(LLMConfig(model_name="openai/gpt-4o", max_tokens=100, max_retries=1))
    services = StageServices(
        driver._identity, None, client, builder, AsyncMock(), lambda: [], MagicMock()
    )
    driver._services = services
    driver._stages.update(
        {
            "think": partial(think_stage, services),
            "plan": partial(plan_stage, services),
            "critique": partial(critique_stage, services),
        }
    )
    driver._pressure = ContextPressure(client, journal, 262144)
    store = driver._tool_executor.get_tool("someTool")._executor.store
    return driver, journal, store


async def scoped_session(store: Any, sid: str, limit: int = 10000) -> ExecutionScope:
    scope = ExecutionScope(store, sid, limit, "total")
    await scope.initialize()
    return scope


async def test_model_receipt_replays_after_stage_return_before_response_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = AsyncMock(return_value=provider_response())
    monkeypatch.setattr("litellm.acompletion", provider)
    driver, journal, store = real_driver(tmp_path, monkeypatch)
    scope = await scoped_session(store, "stage-crash")
    original = journal.append

    async def crash(sid: str, event_type: str, **payload: Any) -> None:
        if event_type == "llm/response":
            raise OSError("response journal lost")
        await original(sid, event_type, **payload)

    monkeypatch.setattr(journal, "append", crash)
    with bind_execution(scope), pytest.raises(OSError, match="response journal lost"):
        await driver.step_session(_session("stage-crash"))
    assert provider.await_count == 1
    restarted, restored_journal, restored_store = real_driver(tmp_path, monkeypatch)
    replay = await restored_journal.resume("stage-crash")
    assert replay is not None
    session = await _make_scheduler(restored_journal)._restore_session(replay)
    with bind_execution(await scoped_session(restored_store, session.id)):
        await restarted.step_session(session)
    assert provider.await_count == 1
    assert session.token_usage == restored_store.budget_usage(session.id)["used"] == 2


@pytest.mark.parametrize("changed_persona", [False, True])
async def test_unknown_model_keeps_stable_position_and_never_resends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, changed_persona: bool
) -> None:
    provider = AsyncMock(side_effect=OSError("provider response disappeared"))
    monkeypatch.setattr("litellm.acompletion", provider)
    driver, journal, store = real_driver(tmp_path, monkeypatch)
    with (
        bind_execution(await scoped_session(store, "unknown")),
        pytest.raises(UnknownModelOutcomeError),
    ):
        await driver.step_session(_session("unknown"))
    restarted, restored_journal, restored_store = real_driver(tmp_path, monkeypatch)
    if changed_persona:
        restarted._services._context_builder.get_system_prompt.return_value = "changed persona"
    replay = await restored_journal.resume("unknown")
    assert replay is not None and not replay.completed and replay.iteration == 0
    session = await _make_scheduler(restored_journal)._restore_session(replay)
    error = UnknownModelOutcomeError
    with bind_execution(await scoped_session(restored_store, "unknown")), pytest.raises(error):
        await restarted.step_session(session)
    assert provider.await_count == 1
    assert not any(
        event["type"] == "session/end" for event in await restored_journal.read_events("unknown")
    )


async def test_declared_writes_order_while_read_runs_and_publish_cancel_tracks_all_children(
    tmp_path: Path,
) -> None:
    driver, _, publisher, _ = _make_driver(tmp_path, [])
    entered, release, read_done = asyncio.Event(), asyncio.Event(), asyncio.Event()
    order: list[str] = []

    async def business(ctx: CallContext, args: dict[str, Any]) -> str:
        label = args["label"]
        if label == "read":
            read_done.set()
            return label
        order.append(label)
        if label == "first":
            entered.set()
            await release.wait()
        return label

    schema = {
        "type": "object",
        "properties": {"label": {"type": "string"}},
        "required": ["label"],
        "additionalProperties": False,
    }
    store = driver._tool_executor.get_tool("someTool")._executor.store
    executor = OperationExecutor(
        OperationRegistry(
            [
                Operation(name, name, schema, True, business, effects=frozenset({effect}))
                for name, effect in (("writer", "write"), ("reader", "read"))
            ]
        ),
        CallContext("agent1", "test", "test"),
        store,
    )
    for operation in executor.registry.definitions():
        driver._tool_executor.register_tool(OperationTool(executor, operation.name))
    session = _session("ordered")
    calls = [
        _tc("a", "writer", {"label": "first"}),
        _tc("b", "reader", {"label": "read"}),
        _tc("c", "writer", {"label": "last"}),
    ]
    await driver._apply_outcome(session, None, StageOutcome(tool_dispatches=calls))
    await entered.wait()
    await read_done.wait()
    assert order == ["first"]
    release.set()
    await asyncio.gather(*session.get_pending_tasks().values())
    assert order == ["first", "last"]
    # A separate batch is cancelled while publishing its very first start event.
    entered.clear()
    release.clear()
    order.clear()
    publish_entered = asyncio.Event()

    async def blocked_publish(*args: Any, **kwargs: Any) -> None:
        publish_entered.set()
        await asyncio.Event().wait()

    publisher.publish.side_effect = blocked_publish
    cancelled = _session("publish-cancel")
    dispatch = asyncio.create_task(
        driver._apply_outcome(cancelled, None, StageOutcome(tool_dispatches=[calls[0], calls[2]]))
    )
    await publish_entered.wait()
    assert len(cancelled.get_pending_tasks()) == 2
    dispatch.cancel()
    with pytest.raises(asyncio.CancelledError):
        await dispatch
    tasks = list(cancelled.get_pending_tasks().values())
    await cancelled.cancel_pending()
    assert all(task.done() for task in tasks)
    assert "last" not in order


async def test_stable_program_session_does_not_bypass_unknown_on_reentry(tmp_path: Path) -> None:
    driver, journal, _, _ = _make_driver(tmp_path, [])
    scheduler = _make_scheduler(journal, loop_driver=driver)
    sessions: list[str] = []
    recipe = driver._loop

    async def unknown(session: AgentSession) -> None:
        sessions.append(session.id)
        if not await journal.read_events(session.id):
            await journal.append(
                session.id,
                "session/start",
                context=[{"role": "user", "content": "hi"}],
                recipe=session.recipe_source,
                invocation=session.invocation,
            )
        raise UnknownModelOutcomeError("already dispatched")

    driver.step_session = unknown  # type: ignore[method-assign]
    invocation = {"program_effect": {"run_id": "program", "index": 4}}
    with pytest.raises(UnknownOutcomeError):
        await scheduler.run_invocation(recipe, "work", [], None, invocation)
    with pytest.raises(RecoveryBlockedError):
        await scheduler.run_invocation(recipe, "work", [], None, invocation)
    assert len(sessions) == 1
    assert await journal.find_unfinished() == []
    assert await journal.list_completed() == []
    assert await journal.end_reason(sessions[0]) == "recovery_blocked"
    assert not any(
        event["type"] == "session/end" for event in await journal.read_events(sessions[0])
    )


async def test_forced_compaction_committed_before_stage_journal_restores_retry_position(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.agents.test_context_pressure import _bad_request

    provider = AsyncMock(
        side_effect=[
            _bad_request("maximum context length exceeded"),
            provider_response("summary"),
            provider_response("done"),
        ]
    )
    monkeypatch.setattr("litellm.acompletion", provider)
    driver, journal, store = real_driver(tmp_path, monkeypatch)
    scope = await scoped_session(store, "compaction-crash")
    session = _session(scope.root_id)
    session.context = [
        {"role": "user", "content": f"{index}: history " * 60} for index in range(30)
    ]
    session.anchor_len = 1
    await journal.append(
        session.id, "session/start", context=session.context, anchor_len=1, recipe="unused"
    )
    driver._pressure._threshold = 100
    compact = driver._pressure.maybe_compact

    async def force_only(current: AgentSession, *, force: bool = False) -> bool:
        return await compact(current, force=True) if force else False

    monkeypatch.setattr(driver._pressure, "maybe_compact", force_only)
    append = journal.append

    async def crash_after_compaction(sid: str, event_type: str, **payload: Any) -> None:
        if event_type == "llm/response":
            raise OSError("stage result journal lost")
        await append(sid, event_type, **payload)

    monkeypatch.setattr(journal, "append", crash_after_compaction)
    with bind_execution(scope), pytest.raises(OSError, match="stage result journal lost"):
        await driver.step_session(session)
    assert provider.await_count == 3
    compact_event = next(
        event
        for event in await journal.read_events(session.id)
        if event["type"] == "context/compact"
    )
    assert compact_event["execution_path"] == ["round:0:stage:0:think:compaction:forced"]
    restarted, restored_journal, restored_store = real_driver(tmp_path, monkeypatch)
    replay = await restored_journal.resume(session.id)
    assert replay is not None
    restored = await _make_scheduler(restored_journal)._restore_session(replay)
    with bind_execution(await scoped_session(restored_store, session.id)):
        await restarted.step_session(restored)
    assert provider.await_count == 3
    assert restored.token_usage == restored_store.budget_usage(session.id)["used"] == 4


async def test_session_has_independent_budget_and_ending_it_does_not_cancel_parent(
    tmp_path: Path,
) -> None:
    from tests.agents.test_session_journal import _CompletingDriver

    _, journal, _, _ = _make_driver(tmp_path, [])
    driver = _CompletingDriver()
    scheduler = _make_scheduler(journal, loop_driver=driver)
    store = ProgramStore(None, build_id="independent")
    parent = ExecutionScope(store, "parent", 1, "output")
    await parent.initialize()
    children: list[ExecutionScope] = []

    async def factory(sid: str, recipe: Any) -> ExecutionScope:
        child = ExecutionScope(store, sid, recipe.max_tokens, "total", parent=parent)
        await child.initialize()
        children.append(child)
        return child

    scheduler._execution_factory = factory
    from tests.agents.test_session_journal import SESSION_PROGRAM_EDN
    from tests.recipe_helpers import recipe_for
    from tlon.agents.member_spec import MemberSpec
    from tlosure.edn import loads

    recipe = recipe_for(MemberSpec.from_edn(loads(SESSION_PROGRAM_EDN)).rules[0])
    with bind_execution(parent):
        await scheduler.run_invocation(recipe, "work", [], None, {})
    parent.check()
    assert not parent.cancelled.is_set() and children[0].cancelled.is_set()
    assert store.budget_usage("parent")["token_limit"] == 1
    assert store.budget_usage(children[0].root_id)["token_limit"] == recipe.max_tokens


async def test_full_unknown_reservation_is_not_misreported_as_successful_budget_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace

    provider = AsyncMock(side_effect=OSError("response unknown"))
    monkeypatch.setattr("litellm.acompletion", provider)
    driver, journal, store = real_driver(tmp_path, monkeypatch)
    driver._loop = replace(driver._loop, max_tokens=100)
    with (
        bind_execution(await scoped_session(store, "full-unknown", 100)),
        pytest.raises(UnknownModelOutcomeError),
    ):
        await driver.step_session(_session("full-unknown"))
    assert store.budget_usage("full-unknown")["remaining"] == 0
    from tlosure.edn import loads

    driver._loop = replace(driver._loop, until=loads("(>= (get state :token_usage) (get limits :max_tokens))"))
    replay = await journal.resume("full-unknown")
    assert replay is not None
    restored = await _make_scheduler(journal)._restore_session(replay)
    with (
        bind_execution(await scoped_session(store, "full-unknown", 100)),
        pytest.raises(UnknownModelOutcomeError),
    ):
        await driver.step_session(restored)
    assert provider.await_count == 1
    assert not restored.is_completed
    assert not any(
        event["type"] == "session/end" for event in await journal.read_events(restored.id)
    )


async def test_fresh_budget_refusal_ends_session_without_provider_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace

    provider = AsyncMock(return_value=provider_response())
    monkeypatch.setattr("litellm.acompletion", provider)
    driver, journal, store = real_driver(tmp_path, monkeypatch)
    driver._loop = replace(driver._loop, max_tokens=1)
    session = _session("no-model-allowance")
    with bind_execution(await scoped_session(store, session.id, 1)):
        await driver.step_session(session)
    assert session.is_completed
    provider.assert_not_awaited()
    assert await journal.end_reason(session.id) == "max_tokens"


async def test_same_stage_location_cannot_switch_prompt_to_bypass_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = AsyncMock(side_effect=OSError("unknown response"))
    monkeypatch.setattr("litellm.acompletion", provider)
    driver, _, store = real_driver(tmp_path, monkeypatch)
    scope = await scoped_session(store, "frozen-stage")
    path = "round:0:stage:0:think:attempt:0"
    messages = [{"role": "user", "content": "work"}]
    with bind_execution(scope.child(path)), pytest.raises(UnknownModelOutcomeError):
        await driver._services.think(messages, span="test", session_id=scope.root_id)
    driver._services._context_builder.get_system_prompt.return_value = "changed persona"
    with bind_execution(scope.child(path)), pytest.raises(UnknownModelOutcomeError):
        await driver._services.think(messages, span="test", session_id=scope.root_id)
    assert provider.await_count == 1
