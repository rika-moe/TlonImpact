"""Ordinary modules preserve whole assistant responses and paired tool messages."""

import json
from typing import Any

import pytest

from tlon.agents.program_package import program_builtins, program_module_sources
from tlosure.builtins import Builtin, EvalError
from tlosure.edn import Keyword, loads
from tlosure.eval import eval_form
from tlosure.modules import ModulePackage


def run(entry: str, arguments: str, builtins: dict[str, Builtin] | None = None) -> Any:
    registry = program_builtins() if builtins is None else builtins
    registry = {
        **registry,
        "model-context": Builtin(lambda env, request: loads("{:fits true}"), pure=False),
    }
    package = ModulePackage.from_sources(
        program_module_sources({"test.tlon": "(ns test (:require [tlon.conversation :as c]))"}),
        registry,
    )
    return eval_form(
        loads(f"(entry {arguments})"), {"entry": package.exports[entry]}, registry, fuel=10000
    )


CALLS = '[{:id "a" :name "one" :arguments {:value 1}} {:id "b" :name "two" :arguments {}}]'
RESPONSE = '{:content nil :reasoning_content "private reasoning" :tool_calls ' + CALLS + "}"
RESULTS = '[{:id "a" :name "one" :success true :data 1 :error nil} {:id "b" :name "two" :success false :data nil :error {:message "rejected"}}]'


def test_full_response_and_failed_result_pairing() -> None:
    history = run("tlon.messages/exchange", f"[] {RESPONSE} {RESULTS}")
    assert len(history) == 3
    assistant = history[0]
    assert assistant[Keyword("content")] is None
    assert assistant[Keyword("reasoning_content")] == "private reasoning"
    calls = assistant[Keyword("tool_calls")]
    assert [call[Keyword("id")] for call in calls] == ["a", "b"]
    assert json.loads(calls[0][Keyword("function")][Keyword("arguments")]) == {"value": 1}
    assert [message[Keyword("tool_call_id")] for message in history[1:]] == ["a", "b"]
    assert json.loads(history[2][Keyword("content")])["success"] is False


@pytest.mark.parametrize(
    "results",
    [
        "[]",
        RESULTS.replace(':id "b"', ':id "a"'),
        RESULTS.replace(':name "two"', ':name "wrong"'),
        RESULTS.replace(':id "a"', ':id "z"'),
    ],
)
def test_incomplete_duplicate_and_reordered_results_are_rejected(results: str) -> None:
    with pytest.raises(EvalError):
        run("tlon.messages/exchange", f"[] {RESPONSE} {results}")


def test_duplicate_call_ids_are_rejected() -> None:
    with pytest.raises(EvalError, match="duplicate"):
        run("tlon.messages/response", RESPONSE.replace(':id "b"', ':id "a"'))


def test_conversation_pairs_data_before_next_request() -> None:
    requests: list[Any] = []
    executions: list[Any] = []

    def model(env: dict[str, Any], request: Any) -> Any:
        requests.append(request)
        return loads(RESPONSE if len(requests) == 1 else '{:content "draft" :tool_calls []}')

    def execute(env: dict[str, Any], request: Any) -> Any:
        executions.append(request)
        return loads(RESULTS)

    registry = program_builtins()
    registry.update(
        {"model-call": Builtin(model, pure=False), "tool-execute": Builtin(execute, pure=False)}
    )
    result = run(
        "tlon.conversation/run",
        '{:messages [{:role "user" :content "task"}] :tools ["one" "two"] :max_tokens 128 :max_rounds 3}',
        registry,
    )
    assert result[Keyword("content")] == "draft"
    assert result[Keyword("reason")] == "complete"
    assert len(requests) == 2 and len(executions) == 1
    assert len(requests[1][Keyword("messages")]) == 4


def test_local_finish_does_not_dispatch_host_control() -> None:
    registry = program_builtins()
    registry["model-call"] = Builtin(
        lambda env, request: loads(
            '{:content "done" :tool_calls [{:id "stop" :name "finish-turn" :arguments {}}]}'
        ),
        pure=False,
    )
    registry["tool-execute"] = Builtin(
        lambda env, request: pytest.fail("control dispatch"), pure=False
    )
    result = run(
        "tlon.conversation/run",
        '{:messages [] :tools ["finish-turn"] :max_tokens 128 :max_rounds 2}',
        registry,
    )
    assert result[Keyword("reason")] == "finish-turn"
    assert len(result[Keyword("messages")]) == 2
    with pytest.raises(EvalError, match="mix"):
        registry["model-call"] = Builtin(
            lambda env, request: loads(
                '{:content nil :tool_calls [{:id "a" :name "finish-turn" :arguments {}} {:id "b" :name "one" :arguments {}}]}'
            ),
            pure=False,
        )
        run(
            "tlon.conversation/run",
            '{:messages [] :tools ["finish-turn" "one"] :max_tokens 128 :max_rounds 2}',
            registry,
        )


def test_round_limit_returns_complete_paired_history() -> None:
    registry = program_builtins()
    registry["model-call"] = Builtin(lambda env, request: loads(RESPONSE), pure=False)
    registry["tool-execute"] = Builtin(lambda env, request: loads(RESULTS), pure=False)
    result = run(
        "tlon.conversation/run",
        '{:messages [] :tools ["one" "two"] :max_tokens 128 :max_rounds 1}',
        registry,
    )
    assert result[Keyword("reason")] == "round-limit"
    assert result[Keyword("content")] is None
    assert len(result[Keyword("messages")]) == 3
    with pytest.raises(EvalError, match="positive integer"):
        run("tlon.conversation/run", "{:messages [] :max_tokens 128 :max_rounds 0}", registry)


def test_pure_program_does_not_link_effectful_conversation_by_comment() -> None:
    from tlosure.builtins import ARITHMETIC_BUILTINS, CORE_BUILTINS

    source = "(ns work.pure (:export run)) ; tlon.conversation\n(defn run [value] value)"
    sources = program_module_sources({"work/pure.tlon": source})
    assert "tlon/conversation.tlon" not in sources
    registry = {**CORE_BUILTINS, **ARITHMETIC_BUILTINS}
    package = ModulePackage.from_sources(sources, registry)
    assert eval_form(loads("(entry 42)"), {"entry": package.entry("work.pure/run")}, registry) == 42


def test_explicit_import_links_conversation_and_messages() -> None:
    sources = program_module_sources(
        {"work/main.tlon": "(ns work.main (:require [tlon.conversation :as conversation]))"}
    )
    assert {"tlon/conversation.tlon", "tlon/messages.tlon"} <= set(sources)


def test_finish_turn_cannot_hide_nonempty_parameters() -> None:
    registry = program_builtins()
    registry["model-call"] = Builtin(
        lambda env, request: loads(
            '{:content nil :tool_calls [{:id "stop" :name "finish-turn" :arguments {:sleep 20}}]}'
        ),
        pure=False,
    )
    registry["tool-execute"] = Builtin(
        lambda env, request: pytest.fail("control dispatch"), pure=False
    )
    with pytest.raises(EvalError, match="arguments must be empty"):
        run(
            "tlon.conversation/run",
            '{:messages [] :tools ["finish-turn"] :max_tokens 128 :max_rounds 2}',
            registry,
        )


@pytest.mark.parametrize(
    "call",
    [
        '{:id 1 :name "one" :arguments {}}',
        '{:id "a" :name nil :arguments {}}',
        '{:id "a" :name "one" :arguments []}',
    ],
)
def test_message_conversion_rejects_malformed_calls(call: str) -> None:
    with pytest.raises(EvalError):
        run("tlon.messages/response", "{:content nil :tool_calls [" + call + "]}")


@pytest.mark.parametrize("limit", ["1.5", "true", "nil", "-1", "0"])
def test_conversation_round_limit_requires_positive_integer(limit: str) -> None:
    registry = program_builtins()
    registry["model-call"] = Builtin(
        lambda env, request: pytest.fail("invalid limit called model"), pure=False
    )
    registry["tool-execute"] = Builtin(
        lambda env, request: pytest.fail("invalid limit dispatched tools"), pure=False
    )
    with pytest.raises(EvalError, match="positive integer"):
        run(
            "tlon.conversation/run",
            "{:messages [] :tools [] :max_tokens 128 :max_rounds " + limit + "}",
            registry,
        )
