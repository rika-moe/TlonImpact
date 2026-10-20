"""Durable program effects preserve committed state and actual mail receipts."""

import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tlon.agents.program_store import ProgramStore
from tlon.agents.vocabulary import make_member_builtins
from tlon.base.config import LLMConfig
from tlon.base.llm_client import LLMClient
from tlon.operations.core import CallContext, OperationExecutor, OperationRegistry
from tlon.operations.invocation import Invocation
from tlon.operations.model import make_model_operations
from tlon.operations.schema import ReplayConflictError
from tlon.organization.mail.mail import Mail
from tlosure.builtins import EvalError
from tlosure.edn import loads
from tlosure.eval import eval_form


@pytest.mark.parametrize("disk", [False, True])
def test_store_freezes_inputs_and_rejects_effect_drift(tmp_path: Path, disk: bool) -> None:
    store = ProgramStore(tmp_path / "program.sqlite3" if disk else None)
    first = store.ensure("r", "original", "input")
    assert store.ensure("r", "changed", "new") == first
    assert store.get_effect("r", 0, "read", "[]") == (False, None)
    store.record_effect("r", 0, "read", "[]", "nil")
    assert store.get_effect("r", 0, "read", "[]") == (True, "nil")
    with pytest.raises(ReplayConflictError, match="changed"):
        store.get_effect("r", 0, "different", "[]")
    with pytest.raises(ReplayConflictError, match="changed"):
        store.get_effect("r", 0, "read", "[1]")
    store.complete("r")
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
                content="edition", usage={"completion_tokens": 4}, tool_calls=[]
            )
        )
    )
    system = AsyncMock(return_value="identity + rules + protocol")
    executor = OperationExecutor(
        OperationRegistry(make_model_operations(client, system)),
        CallContext("a", "run", "call"),
    )
    result = await executor.call("model-call", {"prompt": "source data", "max_tokens": 12})
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


def test_program_words_validate_and_mail_field_uses_real_mail() -> None:
    builtins = make_member_builtins()
    mail = Mail.create(from_address="a@tlon.internal", to=["b@tlon.internal"], content="real")
    assert eval_form(loads("(mail-field msg :id)"), {"msg": mail}, builtins) == mail.id
    assert eval_form(loads("(mail-field msg :content)"), {"msg": None}, builtins) is None
    with pytest.raises(EvalError, match="unknown field"):
        eval_form(loads("(mail-field msg :made-up)"), {"msg": mail}, builtins)
    assert not builtins["state-get"].pure


@pytest.mark.parametrize("disk", [False, True])
def test_failed_run_is_terminal_without_erasing_committed_facts(tmp_path: Path, disk: bool) -> None:
    database = tmp_path / "program.sqlite3" if disk else None
    store = ProgramStore(database)
    store.ensure("bad", "original", "input")
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
    receipt = '{:status "accepted" :mail_id "real-committed-mail"}'
    store.record_effect("bad", 1, "mail-send", '[{:request_id "stable"}]', receipt)
    store.fail("bad", "division by zero")
    restarted = ProgramStore(database) if disk else store
    failed = restarted.ensure("bad", "edited", "other input")
    assert failed.rule_edn == "original" and failed.input_edn == "input"
    assert failed.error == "division by zero" and not failed.completed
    assert not restarted.pending()
    assert restarted.state_get("key") == 1
    assert restarted.get_effect("bad", 1, "mail-send", '[{:request_id "stable"}]') == (
        True,
        receipt,
    )
    # A late completion cannot turn failure into a success or revive it.
    restarted.complete("bad")
    restarted.fail("bad", "later error")
    assert restarted.ensure("bad", "edited", "input").error == "division by zero"
    assert not restarted.ensure("bad", "edited", "input").completed
    restarted.ensure("good", "new program", "new input")
    assert [run.run_id for run in restarted.pending()] == ["good"]
    restarted.complete("good")
    assert restarted.ensure("good", "ignored", "ignored").completed
    assert not restarted.pending()


@pytest.mark.parametrize("disk", [False, True])
def test_batch_registration_keeps_order_and_existing_frozen_records(
    tmp_path: Path, disk: bool
) -> None:
    store = ProgramStore(tmp_path / "program.sqlite3" if disk else None)
    original = store.ensure("first", "original", "old input")
    batch = store.ensure_many([("first", "edited", "new"), ("second", "source", "input")])
    assert batch[0] == original
    assert [run.run_id for run in batch] == ["first", "second"]
    assert [run.run_id for run in store.pending()] == ["first", "second"]
    assert store.ensure_many([]) == []


def test_batch_registration_rolls_back_all_rules_on_mid_batch_failure(tmp_path: Path) -> None:
    database = tmp_path / "program.sqlite3"
    store = ProgramStore(database)
    store.ensure("existing", "source", "input")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TRIGGER fail_second BEFORE INSERT ON runs WHEN NEW.run_id='second' BEGIN SELECT RAISE(ABORT, 'interrupted batch'); END;"
        )
    with pytest.raises(sqlite3.IntegrityError, match="interrupted batch"):
        store.ensure_many([("first", "s1", "i1"), ("second", "s2", "i2"), ("third", "s3", "i3")])
    assert [run.run_id for run in ProgramStore(database).pending()] == ["existing"]
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TRIGGER fail_second")
    registered = store.ensure_many([("first", "s1", "i1"), ("second", "s2", "i2")])
    assert [run.run_id for run in registered] == ["first", "second"]


@pytest.mark.parametrize("disk", [False, True])
def test_durable_effect_identity_and_results_have_typed_conflicts(
    tmp_path: Path, disk: bool
) -> None:
    store = ProgramStore(tmp_path / "conflict.sqlite" if disk else None)
    store.record_effect("r", 0, "read", "[]", "1")
    with pytest.raises(ReplayConflictError):
        store.record_effect("r", 0, "read", "[]", "2")
    call = Invocation("write", "r", ("1",), "write", "{}", "definition", store.build_id)
    assert store.begin_invocation(call)
    from dataclasses import replace

    from tlon.operations.schema import RecoveryMismatchError

    with pytest.raises(RecoveryMismatchError):
        store.begin_invocation(replace(call, arguments_json='{"x":1}'))
