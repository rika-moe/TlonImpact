"""Control boundaries reject composition before effects and resume durable declarations."""

from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from tlon.agents.capabilities.spec import CapabilityArtifact
from tlon.agents.member_spec import MemberRule, MemberSpec
from tlon.agents.program_driver import ProgramDriver
from tlon.agents.program_store import ProgramStore
from tlon.agents.vocabulary import make_member_builtins
from tlon.lifecycle.service import LifecycleService
from tlon.operations.composite import compile_composites
from tlon.operations.core import CallContext, Operation, OperationExecutor, OperationRegistry
from tlon.operations.definitions import host_operation_definitions
from tlon.operations.dsl import DSLAdapter
from tlon.operations.lifecycle import make_lifecycle_operations
from tlon.operations.schema import DefinitionError
from tlosure.builtins import EvalError
from tlosure.edn import loads


@pytest.fixture(params=[False, True], ids=["memory", "sqlite"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> ProgramStore:
    return ProgramStore(
        tmp_path / "control.sqlite3" if request.param else None, build_id="test-build"
    )


def _rule(actions: str, *, when: str | None = None) -> MemberRule:
    gate = "" if when is None else f":when {when} "
    return MemberSpec.from_edn(
        loads(
            '{:version 1 :rules [{:name "control" :on :mail-delivered '
            + gate
            + ":actions ["
            + actions
            + "]}]}"
        )
    ).rules[0]


def _driver(
    store: ProgramStore,
    service: LifecycleService,
    callback: AsyncMock,
    effects: list[str],
) -> ProgramDriver:
    async def side_effect(context: CallContext, arguments: dict[str, Any]) -> str:
        effects.append(context.call_id)
        return "2099-01-01T00:00:00Z"

    operation = Operation(
        "side-effect",
        "Observable test effect",
        {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        {"type": "string"},
        side_effect,
        effects=frozenset({"write"}),
    )
    adapter = DSLAdapter(
        OperationExecutor(
            OperationRegistry((*make_lifecycle_operations(service), operation)),
            CallContext("worker", "run", "call"),
            store,
        )
    )
    return ProgramDriver(
        store,
        "run",
        {**make_member_builtins(), **adapter.builtins()},
        AsyncMock(side_effect=AssertionError("legacy action dispatcher must not run")),
        operations=adapter,
        member_id="worker",
        on_control=callback,
    )


@pytest.mark.parametrize(
    "source,expected",
    [
        ("(finish-turn {})", {"action": "finish", "reason": ""}),
        (
            '(sleep-until {:wake_at "2099-01-01T00:00:00Z" :reason "rest"})',
            {"action": "sleep", "reason": "rest", "wake_at": "2099-01-01T00:00:00+00:00"},
        ),
        ('(exit-agent {:reason "done"})', {"action": "exit", "reason": "done"}),
    ],
)
async def test_sole_direct_literal_control_declares_transition(
    store: ProgramStore, source: str, expected: dict[str, Any]
) -> None:
    callback = AsyncMock()
    effects: list[str] = []
    service = LifecycleService("worker")
    await _driver(store, service, callback, effects).run(_rule(source), {})
    callback.assert_awaited_once_with(expected)
    assert effects == []


@pytest.mark.parametrize(
    "source",
    [
        "(side-effect {}) (finish-turn {})",
        "(finish-turn {}) (side-effect {})",
        "(if true (finish-turn {}) nil)",
        "(let [f (fn [] (finish-turn {}))] (f))",
        "((fn [] (exit-agent {})))",
        "(while [n 0] (< n 1) (finish-turn {}) (recur (+ n 1)))",
        "(sleep-until {:wake_at (side-effect {})})",
        "(let [wake_at (side-effect {})] (sleep-until {:wake_at wake_at}))",
    ],
)
async def test_composed_controls_reject_before_any_effect(store: ProgramStore, source: str) -> None:
    callback = AsyncMock()
    effects: list[str] = []
    with pytest.raises(EvalError):
        await _driver(store, LifecycleService("worker"), callback, effects).run(_rule(source), {})
    callback.assert_not_awaited()
    assert effects == []
    assert store.get_effect("run", 0, "side-effect", "[{}]") == (False, None)


@pytest.mark.parametrize(
    "when",
    [
        "(finish-turn {})",
        "(sleep-until {:wake_at (side-effect {})})",
    ],
)
async def test_when_control_rejects_even_with_a_sole_control_action(
    store: ProgramStore, when: str
) -> None:
    callback = AsyncMock()
    effects: list[str] = []
    with pytest.raises(EvalError):
        await _driver(store, LifecycleService("worker"), callback, effects).run(
            _rule("(finish-turn {})", when=when), {}
        )
    callback.assert_not_awaited()
    assert effects == []


async def test_replay_reapplies_declaration_without_repeating_handler(store: ProgramStore) -> None:
    service = LifecycleService("worker")
    callback = AsyncMock(side_effect=[OSError("scheduling store unavailable"), None])
    effects: list[str] = []
    program = _rule('(exit-agent {:reason "done"})')
    with patch.object(service, "check_context", wraps=service.check_context) as handler_entry:
        with pytest.raises(OSError, match="scheduling store unavailable"):
            await _driver(store, service, callback, effects).run(program, {})
        await _driver(store, service, callback, effects).run(program, {})
        handler_entry.assert_called_once()
    assert callback.await_count == 2
    assert callback.await_args_list[0] == callback.await_args_list[1]
    assert effects == []


@pytest.mark.parametrize(
    "body",
    [
        "(finish-turn {})",
        '(sleep-until {:wake_at "2099-01-01T00:00:00Z"})',
        "(exit-agent {})",
        "(run-loop {})",
        "(if true (finish-turn {}) nil)",
    ],
)
def test_impure_capability_cannot_wrap_control_or_session_requests(body: str) -> None:
    artifact = CapabilityArtifact.from_edn(
        '{:version 2 :name "wrapped-control" :description "test" '
        ':input-schema {:type "object" :properties {} :required [] :additionalProperties false} :output-schema true '
        + ":impl (fn [input] "
        + body
        + ")}"
    )
    with pytest.raises(DefinitionError):
        compile_composites((artifact.spec,), host_operation_definitions())


async def test_empty_form_reports_expression_error_without_control_dispatch(
    store: ProgramStore,
) -> None:
    callback = AsyncMock()
    effects: list[str] = []
    with pytest.raises(EvalError):
        await _driver(store, LifecycleService("worker"), callback, effects).run(
            replace(_rule("(finish-turn {})"), actions=(loads("()"),)), {}
        )
    callback.assert_not_awaited()
    assert effects == []
