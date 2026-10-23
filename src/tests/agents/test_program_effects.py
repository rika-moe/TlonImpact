"""Durable program effects preserve committed state and actual mail receipts."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tlon.agents.program_store import ProgramStore
from tlon.agents.vocabulary import make_member_builtins
from tlon.base.config import LLMConfig
from tlon.base.llm_client import LLMClient
from tlon.operations.core import CallContext, OperationExecutor, OperationRegistry
from tlon.operations.invocation import Invocation, InvocationOutcome
from tlon.operations.model import make_model_operations
from tlon.operations.schema import RecoveryMismatchError
from tlon.organization.mail.mail import Mail
from tlosure.builtins import EvalError
from tlosure.edn import loads
from tlosure.eval import eval_form


@pytest.mark.parametrize("disk", [False, True])
def test_store_freezes_inputs_and_rejects_effect_drift(tmp_path: Path, disk: bool) -> None:
    store = ProgramStore(tmp_path / "program.sqlite3" if disk else None)
    first = store.ensure_entry("r", "original", "input", {})
    assert store.ensure_entry("r", "changed", "new", {}) == first
    call = Invocation("read", "r", ("0",), "read", "[]", "definition", store.build_id)
    assert store.lookup_invocation(call) is None
    store.commit_invocation(call, InvocationOutcome(True, value=None))
    outcome = store.lookup_invocation(call)
    assert outcome is not None and outcome.success and outcome.value is None
    for changed in [replace(call, name="different"), replace(call, arguments_json="[1]")]:
        with pytest.raises(RecoveryMismatchError):
            store.lookup_invocation(changed)
    store.complete_entry("r", None)
    assert not store.pending()
    store.close()


def test_state_and_invocation_commit_together_and_replay_does_not_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "program.sqlite3"
    store = ProgramStore(path, build_id="build")
    first = Invocation(
        "c1", "r", ("0",), "state-put", '{"key":"key","value":1}', "definition", "build"
    )
    second = Invocation(
        "c2", "r", ("1",), "state-put", '{"key":"key","value":3}', "definition", "build"
    )
    store.atomic_state_put(first, "key", 1)
    original_record = store._commit

    def fail_record(*args: object) -> None:
        raise OSError("journal disk error")

    monkeypatch.setattr(store, "_commit", fail_record)
    with pytest.raises(OSError, match="journal"):
        store.atomic_state_put(second, "key", 3)
    restarted = ProgramStore(path, build_id="build")
    assert restarted.state_get("key") == 1
    assert restarted.lookup_invocation(second) is None
    monkeypatch.setattr(store, "_commit", original_record)
    store.atomic_state_put(second, "key", 3)
    assert restarted.atomic_state_put(first, "key", 1) == 1
    assert restarted.state_get("key") == 3


async def test_llm_effect_keeps_system_prompt_and_disables_tools() -> None:
    client = SimpleNamespace(
        chat=AsyncMock(
            return_value=SimpleNamespace(
                content="edition",
                usage={"completion_tokens": 4},
                tool_calls=[],
                finish_reason="stop",
                reasoning_content=None,
            )
        )
    )
    system = AsyncMock(return_value="identity + rules + protocol")
    executor = OperationExecutor(
        OperationRegistry(make_model_operations(client, system)),
        CallContext("a", "run", "call"),
    )
    result = await executor.call(
        "model-call", {"messages": [{"role": "user", "content": "source data"}], "max_tokens": 12}
    )
    assert result["usage"]["completion_tokens"] == 4
    kwargs = client.chat.await_args.kwargs
    assert kwargs["system_prompt"] == "identity + rules + protocol"
    assert kwargs["tools"] is None and kwargs["max_tokens"] == 12
    assert kwargs["messages"] == [{"role": "user", "content": "source data"}]


@pytest.mark.parametrize("requested,expected", [(None, 64), (16, 16), (100, 64)])
async def test_provider_receives_real_output_cap(monkeypatch, requested, expected) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "fake")
    raw = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="ok", tool_calls=None, reasoning_content=None),
                finish_reason="stop",
            )
        ],
        usage=None,
    )
    completion = AsyncMock(return_value=raw)
    monkeypatch.setattr("litellm.acompletion", completion)
    client = LLMClient(LLMConfig(model_name="openai/example", max_tokens=64))
    await client.chat("system", [], max_tokens=requested)
    assert completion.await_args.kwargs["max_tokens"] == expected


def test_program_words_validate_and_mail_field_uses_materialized_mail() -> None:
    builtins = make_member_builtins()
    mail = Mail.create(from_address="a@tlon.internal", to=["b@tlon.internal"], content="real")
    assert eval_form(loads("(mail-field msg :id)"), {"msg": mail.to_dict()}, builtins) == mail.id
    with pytest.raises(EvalError, match="materialized mail record"):
        eval_form(loads("(mail-field msg :id)"), {"msg": mail}, builtins)
    assert eval_form(loads("(mail-field msg :content)"), {"msg": None}, builtins) is None
    with pytest.raises(EvalError, match="unknown field"):
        eval_form(loads("(mail-field msg :made-up)"), {"msg": mail}, builtins)
    assert not builtins["state-get"].pure


@pytest.mark.parametrize("disk", [False, True])
def test_failed_run_is_terminal_without_erasing_committed_facts(tmp_path: Path, disk: bool) -> None:
    database = tmp_path / "program.sqlite3" if disk else None
    store = ProgramStore(database)
    store.ensure_entry("bad", "original", "input", {})
    store.atomic_state_put(
        Invocation(
            "state",
            "bad",
            ("0",),
            "state-put",
            '{"key":"key","value":1}',
            "definition",
            store.build_id,
        ),
        "key",
        1,
    )
    receipt = {"status": "accepted", "mail_id": "real-committed-mail"}
    call = Invocation(
        "mail", "bad", ("1",), "mail-send", '{"request_id":"stable"}', "definition", store.build_id
    )
    store.commit_invocation(call, InvocationOutcome(True, value=receipt))
    store.fail("bad", "division by zero")
    restarted = ProgramStore(database) if disk else store
    failed = restarted.ensure_entry("bad", "edited", "other input", {})
    assert failed.rule_edn == "original" and failed.input_edn == "input"
    assert failed.error == "division by zero" and not failed.completed
    assert not restarted.pending()
    assert restarted.state_get("key") == 1
    outcome = restarted.lookup_invocation(call)
    assert outcome is not None and outcome.success and outcome.value == receipt
    # A late completion cannot turn failure into a success or revive it.
    with pytest.raises(RecoveryMismatchError):
        restarted.complete_entry("bad", None)
    restarted.fail("bad", "later error")
    assert restarted.ensure_entry("bad", "edited", "input", {}).error == "division by zero"
    assert not restarted.ensure_entry("bad", "edited", "input", {}).completed
    restarted.ensure_entry("good", "new program", "new input", {})
    assert [run.run_id for run in restarted.pending()] == ["good"]
    restarted.complete_entry("good", None)
    assert restarted.ensure_entry("good", "ignored", "ignored", {}).completed
    assert not restarted.pending()


@pytest.mark.parametrize("disk", [False, True])
def test_durable_effect_identity_and_results_have_typed_conflicts(
    tmp_path: Path, disk: bool
) -> None:
    store = ProgramStore(tmp_path / "conflict.sqlite" if disk else None)
    call = Invocation("write", "r", ("1",), "write", "{}", "definition", store.build_id)
    assert store.begin_invocation(call)
    with pytest.raises(RecoveryMismatchError):
        store.begin_invocation(replace(call, arguments_json='{"x":1}'))
    store.commit_invocation(call, InvocationOutcome(True, value=1))
    with pytest.raises(RecoveryMismatchError):
        store.commit_invocation(call, InvocationOutcome(True, value=2))
