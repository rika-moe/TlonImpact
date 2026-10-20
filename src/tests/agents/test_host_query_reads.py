"""External read admission is separate from deterministic DSL computation."""

from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from tlon.agents.member_spec import MemberSpec, MemberSpecValidationError, validate_member_spec
from tlon.agents.program_driver import ProgramDriver
from tlon.agents.program_store import ProgramStore
from tlon.agents.vocabulary import (
    LOOP_BUILTINS,
    POLICY_BUILTINS,
    STAGE_COND_BUILTINS,
    make_member_builtins,
)
from tlon.operations.invocation import registry_identity
from tlosure.builtins import Builtin, EvalError
from tlosure.edn import loads
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


def spec(when: str, actions: str = "[]") -> MemberSpec:
    return MemberSpec.from_edn(
        loads(
            '{:version 1 :rules [{:name "test" :on :mail-delivered :when '
            + when
            + " :actions "
            + actions
            + "}]}"
        )
    )


@pytest.mark.parametrize(
    "expression", ['(sleeping? "a")', '(running? "a")', '(lazy? "a")', "(member-roster)"]
)
def test_host_reads_are_admitted_in_rule_conditions_only(expression: str) -> None:
    builtins = make_member_builtins(Host())
    validate_member_spec(spec(expression), builtins=builtins)
    word = builtins[str(loads(expression)[0])]
    assert not word.pure and word.read_only
    for registry in [LOOP_BUILTINS, STAGE_COND_BUILTINS, POLICY_BUILTINS]:
        with pytest.raises(EvalError):
            validate_form(loads(expression), registry, scope=set())
    with pytest.raises(MemberSpecValidationError):
        validate_member_spec(spec("(mail-mark-read {:mail_ids []})"), builtins=builtins)


@pytest.mark.parametrize("disk", [False, True])
async def test_host_reads_sample_each_call_and_replay_without_querying(
    tmp_path: Path, disk: bool
) -> None:
    host = Host()
    store = ProgramStore(tmp_path / "program.sqlite" if disk else None, build_id="test")
    mutations: list[bool] = []

    def stop(_env: dict[str, Any]) -> None:
        host.running = False
        mutations.append(False)

    builtins = {**make_member_builtins(host), "change-state": Builtin(stop, pure=False)}
    program = spec(
        '(running? "a")', '[(member-roster) (change-state) (running? "a") (member-roster)]'
    )
    validate_member_spec(program, builtins=builtins)
    original = ProgramDriver(store, "root", builtins, AsyncMock())
    assert original.builtins["running?"] is builtins["running?"]
    await original.run(program.rules[0], {})
    assert host.samples == [
        ("running", True),
        ("roster", "True"),
        ("running", False),
        ("roster", "False"),
    ]
    assert mutations == [False]
    host.samples.clear()
    restarted = ProgramStore(store.database, build_id="test") if disk else store
    await ProgramDriver(restarted, "root", builtins, AsyncMock()).run(program.rules[0], {})
    assert host.samples == []
    assert mutations == [False]


def test_read_only_metadata_is_frozen_in_registry_identity() -> None:
    builtin = Builtin(lambda _env: True, pure=False, read_only=True)
    assert registry_identity({"query": builtin}) != registry_identity(
        {"query": replace(builtin, read_only=False)}
    )
