"""External read admission is separate from deterministic DSL computation."""

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tlon.agents.program_driver import ProgramDriver
from tlon.agents.program_store import ProgramStore
from tlon.agents.vocabulary import (
    SCRIPT_BUILTINS,
    make_member_builtins,
)
from tlon.operations.core import CallContext, OperationExecutor, OperationRegistry
from tlon.operations.dsl import DSLAdapter
from tlon.operations.invocation import registry_identity
from tlon.operations.registry import make_registry_query_operations
from tlosure.builtins import Builtin, EvalError
from tlosure.edn import loads
from tlosure.modules import ModulePackage
from tlosure.validate import validate_form


class Host:
    def __init__(self) -> None:
        self.running = True
        self.samples: list[tuple[str, Any]] = []

    def is_running(self, agent_id: Any) -> bool:
        self.samples.append(("running", self.running))
        return self.running

    def is_sleeping(self, agent_id: Any) -> bool:
        return not self.running

    def is_lazy(self, agent_id: Any) -> bool:
        return True

    def start_all_candidates(self) -> list[str]:
        return []

    def member_roster(self) -> str:
        self.samples.append(("roster", str(self.running)))
        return str(self.running)


@pytest.mark.parametrize(
    "expression",
    [
        '(member-sleeping {:agent_id "a"})',
        '(member-running {:agent_id "a"})',
        '(member-lazy {:agent_id "a"})',
        "(member-roster {})",
    ],
)
def test_host_reads_are_not_pure_member_vocabulary(expression: str) -> None:
    builtins = make_member_builtins()
    assert not builtins[str(loads(expression)[0])].pure
    with pytest.raises(EvalError):
        validate_form(loads(expression), SCRIPT_BUILTINS, scope=set())


@pytest.mark.parametrize("disk", [False, True])
async def test_host_reads_sample_each_call_and_replay_without_querying(
    tmp_path: Path, disk: bool
) -> None:
    host = Host()
    store = ProgramStore(tmp_path / "program.sqlite" if disk else None, build_id="test")
    mutations: list[bool] = []

    async def stop(_context: CallContext, _args: dict[str, Any]) -> None:
        host.running = False
        mutations.append(False)

    from tlon.operations.core import Operation

    definitions = (
        *make_registry_query_operations(host),
        Operation(
            "change-state",
            "test mutation",
            {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
            {"type": "null"},
            stop,
            effects=frozenset({"write"}),
        ),
    )

    def adapter_for(target: ProgramStore) -> DSLAdapter:
        return DSLAdapter(
            OperationExecutor(
                OperationRegistry(definitions), CallContext("a", "root", "call"), target
            )
        )

    adapter = adapter_for(store)
    builtins = {**make_member_builtins(), **adapter.builtins()}
    package = ModulePackage.from_sources(
        {
            "work.tlon": '(ns work (:export handle)) (defn handle [event] (let [a (member-roster {}) b (change-state {}) c (member-running {:agent_id "a"})] (member-roster {})))'
        },
        builtins,
    )
    original = ProgramDriver(store, "root", builtins, operations=adapter, member_id="a")
    await original.run_entry(package.entry("work/handle"), {}, fuel=1000)
    assert host.samples == [
        ("roster", "True"),
        ("running", False),
        ("roster", "False"),
    ]
    assert mutations == [False]
    host.samples.clear()
    restarted = ProgramStore(store.database, build_id="test") if disk else store
    replay_adapter = adapter_for(restarted)
    replay_builtins = {**make_member_builtins(), **replay_adapter.builtins()}
    replay_package = ModulePackage.from_sources(
        {
            "work.tlon": '(ns work (:export handle)) (defn handle [event] (let [a (member-roster {}) b (change-state {}) c (member-running {:agent_id "a"})] (member-roster {})))'
        },
        replay_builtins,
    )
    await ProgramDriver(
        restarted, "root", replay_builtins, operations=replay_adapter, member_id="a"
    ).run_entry(replay_package.entry("work/handle"), {}, fuel=1000)
    assert host.samples == []
    assert mutations == [False]


def test_purity_metadata_is_frozen_in_registry_identity() -> None:
    builtin = Builtin(lambda _env: True, pure=False)
    assert registry_identity({"query": builtin}) != registry_identity(
        {"query": replace(builtin, pure=True)}
    )


async def test_program_rejects_unjournaled_effectful_builtin() -> None:
    calls: list[str] = []
    builtins = {"untracked": Builtin(lambda _env: calls.append("called"), pure=False)}
    store = ProgramStore(None)
    adapter = DSLAdapter(
        OperationExecutor(OperationRegistry([]), CallContext("a", "r", "c"), store)
    )
    package = ModulePackage.from_sources(
        {"work.tlon": "(ns work (:export handle)) (defn handle [event] (untracked))"}, builtins
    )
    with pytest.raises(EvalError, match="effectful builtin is not an operation"):
        await ProgramDriver(store, "r", builtins, operations=adapter, member_id="a").run_entry(
            package.entry("work/handle"), {}, fuel=100
        )
    assert calls == []
