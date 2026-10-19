"""Durable program effects preserve committed state and actual mail receipts."""

import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tlon.agents.program_effects import ProgramEffects
from tlon.agents.program_store import ProgramStore
from tlon.agents.vocabulary import make_member_builtins
from tlon.base.config import LLMConfig
from tlon.base.llm_client import LLMClient
from tlon.organization.mail.mail import Mail
from tlosure.builtins import ActionRequest, EvalError
from tlosure.edn import Keyword, dumps, loads
from tlosure.eval import eval_form


@pytest.mark.parametrize("disk", [False, True])
def test_store_freezes_inputs_and_rejects_effect_drift(tmp_path: Path, disk: bool) -> None:
    store = ProgramStore(tmp_path / "program.sqlite3" if disk else None)
    first = store.ensure("r", "original", "input")
    assert store.ensure("r", "changed", "new") == first
    assert store.get_effect("r", 0, "read", "[]") == (False, None)
    store.record_effect("r", 0, "read", "[]", "nil")
    assert store.get_effect("r", 0, "read", "[]") == (True, "nil")
    with pytest.raises(ValueError, match="changed"):
        store.get_effect("r", 0, "different", "[]")
    with pytest.raises(ValueError, match="changed"):
        store.get_effect("r", 0, "read", "[1]")
    store.complete("r")
    assert not store.pending()
    store.close()


def test_state_and_effect_commit_together_and_replay_does_not_overwrite(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "program.sqlite3"
    store = ProgramStore(path)
    store.ensure("r", "source", "input")
    store.atomic_state_put_record("r", 0, "state-put", '["key" 1]', "key", 1)
    original_record = store._record

    def fail_record(*args):
        raise OSError("journal disk error")

    monkeypatch.setattr(store, "_record", fail_record)
    with pytest.raises(OSError, match="journal"):
        store.atomic_state_put_record("r", 1, "state-put", '["key" 2]', "key", 2)
    restarted = ProgramStore(path)
    assert restarted.state_get("key") == 1
    assert restarted.get_effect("r", 1, "state-put", '["key" 2]') == (False, None)
    monkeypatch.setattr(store, "_record", original_record)
    store.atomic_state_put_record("r", 1, "state-put", '["key" 3]', "key", 3)
    assert restarted.atomic_state_put_record("r", 0, "state-put", '["key" 1]', "key", 1) == 1
    assert restarted.state_get("key") == 3


async def test_state_effect_uses_original_call_arguments() -> None:
    store = ProgramStore(None)
    effects = ProgramEffects()
    request = ActionRequest("state_put", {"key": "edition", "value": {Keyword("body"): "fixed"}})
    args = dumps(["edition", request.data["value"]])
    result = await effects.execute(request, "unused", "r", 0, store, args)
    found, recorded = store.get_effect("r", 0, "state-put", args)
    assert found and loads(recorded) == result == store.state_get("edition")


async def test_llm_effect_keeps_system_prompt_and_disables_tools() -> None:
    client = SimpleNamespace(
        chat=AsyncMock(
            return_value=SimpleNamespace(
                content="edition", usage={"completion_tokens": 4}, tool_calls=[]
            )
        )
    )
    system = AsyncMock(return_value="identity + rules + protocol")
    effects = ProgramEffects(llm_client=client, system_prompt=system)
    result = await effects.execute(
        ActionRequest("llm_call", {"prompt": "source data", "max_tokens": 12}),
        "unused",
        "run",
        0,
        ProgramStore(None),
        "[]",
    )
    assert result[Keyword("usage")][Keyword("completion-tokens")] == 4
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
    with pytest.raises(EvalError, match="positive integer"):
        eval_form(loads('(llm-call {:prompt "x" :max-tokens true})'), {}, builtins)
    assert eval_form(loads('(state-get "subscriptions")'), {}, builtins).kind == "state_get"


@pytest.mark.parametrize("disk", [False, True])
def test_failed_run_is_terminal_without_erasing_committed_facts(tmp_path: Path, disk: bool) -> None:
    database = tmp_path / "program.sqlite3" if disk else None
    store = ProgramStore(database)
    store.ensure("bad", "original", "input")
    store.atomic_state_put_record("bad", 0, "state-put", '["key" 1]', "key", 1)
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
