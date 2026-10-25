"""Action loops deliver and finish only through successfully executed tools."""

from collections.abc import Callable
from typing import Any

import pytest

from tlon.agents.program_package import program_builtins, program_module_sources
from tlon.operations.schema import edn_value, json_value
from tlosure.builtins import Builtin
from tlosure.edn import loads
from tlosure.eval import eval_form
from tlosure.modules import ModulePackage


def execute(
    responses: list[dict[str, Any]],
    *,
    succeeds: bool = True,
    method: str = "reviewed",
    max_rounds: int = 3,
    trace: dict[str, Any] | None = None,
    previous: dict[str, Any] | None = None,
    run_id: str = "run-1",
    contract: str = "contract",
    pressure: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    data: dict[str, Any] | None = None,
) -> tuple[list, list]:
    requests: list[dict[str, Any]] = []
    deliveries: list[dict[str, Any]] = []
    marked: list[dict[str, Any]] = []
    if trace is not None:
        trace.update(requests=requests, deliveries=deliveries, marked=marked, tool_calls=[])

    def model(env: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
        requests.append(options)
        return edn_value(responses.pop(0))

    def tools(env: dict[str, Any], options: dict[str, Any]) -> list[dict[str, Any]]:
        results = []
        for call in options["tool_calls"]:
            if trace is not None:
                trace["tool_calls"].append(call)
            if succeeds and call["name"] == "mail-send":
                deliveries.append(call["arguments"])
            results.append(
                {
                    "id": call["id"],
                    "name": call["name"],
                    "success": succeeds,
                    "data": {
                        "local_recipients": [
                            str(address).split("@")[0]
                            for address in call["arguments"].get("to", [])
                        ]
                    }
                    if succeeds
                    else None,
                    "error": None if succeeds else "delivery failed",
                }
            )
        return results

    def send(env: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
        deliveries.append(options)
        return {"local_recipients": ["sender"]}

    builtins = program_builtins()
    builtins.update(
        {
            "model-call": Builtin(model, pure=False),
            "model-context": Builtin(
                lambda env, options: (
                    pressure(options)
                    if pressure
                    else {
                        "fits": True,
                        "input_tokens": 1000,
                        "context_window": 100000,
                        "output_tokens": 32000,
                        "token_budget_remaining": 100000,
                        "token_budget_limit": 100000,
                        "token_budget_measure": "output",
                    }
                ),
                pure=False,
            ),
            "run-previous": Builtin(
                lambda env, options: edn_value(
                    {"run_id": run_id, "contract": contract, "previous": previous}
                ),
                pure=False,
            ),
            "tool-execute": Builtin(tools, pure=False),
            "mail-send": Builtin(send, pure=False),
            "mail-mark-read": Builtin(lambda env, options: marked.append(options), pure=False),
            "member-context": Builtin(
                lambda env, options: (
                    data
                    or {
                        "knowledge": "known fact",
                        "lessons": "lesson",
                        "skills": "skill",
                        "current_task": "task",
                        "thread_recap": "recap",
                        "mail_history": [],
                        "tools": ["mail-send", "finish-turn"],
                    }
                ),
                pure=False,
            ),
        }
    )
    sources = program_module_sources(
        {
            "test.tlon": """
      (ns test (:require [tlon.agent :as agent]) (:export handle))
      (defn handle [event]
        (agent/respond event {:method (get event :method) :max_rounds (get event :max_rounds)}))
    """
        }
    )
    package = ModulePackage.from_sources(sources, builtins)
    event = {
        "mail": {"id": "mail", "content": "hello", "to": ["member@tlon.internal"]},
        "sender": "sender@tlon.internal",
        "method": method,
        "max_rounds": max_rounds,
        "self": {"address": "member@tlon.internal"},
    }
    result = eval_form(
        loads("(handle event)"),
        {"handle": package.entry("test/handle"), "event": event},
        builtins,
        fuel=10000,
    )
    if trace is not None:
        trace["result"] = json_value(result, edn_keys=True)["result"]
    return requests, deliveries


def call() -> dict[str, Any]:
    return {
        "content": None,
        "tool_calls": [
            {
                "id": "send",
                "name": "mail-send",
                "arguments": {"to": ["sender@tlon.internal"], "content": "unreviewed"},
            }
        ],
    }


def finish() -> dict[str, Any]:
    return {"tool_calls": [{"id": "done", "name": "finish-turn", "arguments": {}}]}


def test_original_tool_arguments_are_executed_without_hidden_review() -> None:
    trace: dict[str, Any] = {}
    requests, deliveries = execute([call(), finish()], trace=trace)
    assert len(requests) == 2
    assert [delivery["content"] for delivery in deliveries] == ["unreviewed"]
    assert "Skills:" in requests[0]["messages"][0]["content"]
    assert "known fact" in requests[0]["messages"][1]["content"]
    assert requests[1]["messages"][: len(requests[0]["messages"])] == requests[0]["messages"]
    assert [item["name"] for item in trace["tool_calls"]] == ["mail-send", "finish-turn"]
    assert trace["marked"] == [{"mail_ids": ["mail"]}]
    assert trace["result"]["messages"] == requests[1]["messages"] + trace["result"]["messages"][-2:]


@pytest.mark.parametrize("content", [None, "", "answer"])
def test_text_without_tool_call_never_delivers_or_marks_read(content: str | None) -> None:
    from tlosure.eval import EvalError

    trace: dict[str, Any] = {}
    with pytest.raises(EvalError, match="requires tool calls"):
        execute([{"content": content}], trace=trace)
    assert trace["marked"] == trace["deliveries"] == trace["tool_calls"] == []


def test_failed_finish_never_marks_read() -> None:
    from tlosure.eval import EvalError

    trace: dict[str, Any] = {}
    with pytest.raises(EvalError, match="finish-turn execution failed"):
        execute([finish()], succeeds=False, trace=trace)
    assert trace["marked"] == []


@pytest.mark.parametrize("response", [call(), finish(), {"content": "partial"}])
def test_truncated_action_never_executes_tools_or_marks_read(response: dict[str, Any]) -> None:
    from tlosure.eval import EvalError

    trace: dict[str, Any] = {}
    with pytest.raises(EvalError, match="model output truncated"):
        execute([{**response, "finish_reason": "length"}], trace=trace)
    assert trace["marked"] == trace["deliveries"] == trace["tool_calls"] == []


def test_truncated_plan_cannot_trigger_actions() -> None:
    from tlosure.eval import EvalError

    trace: dict[str, Any] = {}
    with pytest.raises(EvalError, match="model output truncated"):
        execute(
            [{"content": "partial", "finish_reason": "length"}], method="plan-then-act", trace=trace
        )
    assert trace["marked"] == trace["deliveries"] == trace["tool_calls"] == []


def test_peer_delivery_does_not_create_an_automatic_reply() -> None:
    response = call()
    response["tool_calls"][0]["arguments"]["to"] = ["peer@tlon.internal"]
    _, deliveries = execute([response, finish()])
    assert [delivery["content"] for delivery in deliveries] == ["unreviewed"]


def test_finish_turn_is_a_local_signal_without_an_extra_model_call() -> None:
    requests, deliveries = execute(
        [
            {
                "content": None,
                "tool_calls": [{"id": "done", "name": "finish-turn", "arguments": {}}],
            }
        ]
    )
    assert len(requests) == 1
    assert deliveries == []


def test_plan_is_computed_once_before_tool_execution() -> None:
    requests, deliveries = execute(
        [
            {"content": "plan"},
            call(),
            finish(),
        ],
        method="plan-then-act",
    )
    assert len(requests) == 3
    assert requests[0]["tools"] == []
    assert "Plan at most five steps" in requests[0]["messages"][-1]["content"]
    assert "Execute the plan" in requests[1]["messages"][-1]["content"]
    assert requests[1]["messages"][: len(requests[0]["messages"])] == requests[0]["messages"]
    assert len(deliveries) == 1


@pytest.mark.parametrize("method", ["reviewed", "plan-then-act"])
def test_round_limit_fails_without_marking_mail_read_or_delivering(method: str) -> None:
    from tlosure.eval import EvalError

    trace: dict[str, Any] = {}
    rounds = [
        {
            "content": None,
            "tool_calls": [
                {"id": f"round-{round_number}-tool-{index}", "name": "time-now", "arguments": {}}
                for index in range(5)
            ],
        }
        for round_number in range(10)
    ]
    if method == "plan-then-act":
        rounds.insert(0, {"content": "plan", "tool_calls": []})
    with pytest.raises(EvalError, match="agent work round-limit reached before completion"):
        execute(rounds, max_rounds=10, method=method, trace=trace)
    assert len(trace["requests"]) == 10 + int(method == "plan-then-act")
    assert len(trace["tool_calls"]) == 50
    assert trace["marked"] == []
    assert trace["deliveries"] == []


@pytest.mark.parametrize("mixed", [True, False])
def test_malformed_finish_never_executes_or_marks_read(mixed: bool) -> None:
    from tlosure.eval import EvalError

    response = finish()
    if mixed:
        response["tool_calls"].extend(call()["tool_calls"])
    else:
        response["tool_calls"][0]["arguments"] = {"extra": True}
    trace: dict[str, Any] = {}
    with pytest.raises(EvalError, match="empty standalone"):
        execute([response], trace=trace)
    assert trace["marked"] == trace["deliveries"] == trace["tool_calls"] == []


def test_successive_runs_reuse_complete_prefix_and_append_new_materials() -> None:
    first: dict[str, Any] = {}
    first_requests, _ = execute([call(), finish()], trace=first)
    second: dict[str, Any] = {}
    second_requests, _ = execute(
        [finish()],
        previous={"run_id": "run-1", "contract": "contract", "result": first["result"]},
        run_id="run-2",
        trace=second,
    )
    assert (
        second_requests[0]["messages"][: len(first["result"]["messages"])]
        == first["result"]["messages"]
    )
    appended = second_requests[0]["messages"][len(first["result"]["messages"]) :]
    assert "Source run: run-2" in appended[0]["content"]
    assert "Current Skills:" in appended[0]["content"]
    assert second["result"]["source_run_id"] == "run-2"
    assert second["result"]["contract"] == "contract"
    assert len(first_requests) == 2


def test_changed_contract_starts_fresh_in_stable_then_dynamic_order() -> None:
    prior = {"contract": "old", "messages": [{"role": "user", "content": "stale-contract-prefix"}]}
    requests, _ = execute(
        [finish()],
        previous={"run_id": "old", "contract": "old", "result": prior},
        contract="new",
        data={
            "skills": "stable skills",
            "lessons": "stable lesson",
            "mail_history": [{"role": "user", "content": "original mail history"}],
            "knowledge": "fresh knowledge",
            "current_task": "task",
            "thread_recap": "recap",
            "tools": ["finish-turn"],
        },
    )
    history = requests[0]["messages"]
    assert "stable skills" in history[0]["content"]
    assert history[1]["content"] == "original mail history"
    assert "fresh knowledge" in history[2]["content"]
    assert all("stale-contract-prefix" not in item["content"] for item in history)


def resource_view(
    input_tokens: int = 1000, remaining: int = 10000, measure: str = "output"
) -> dict[str, Any]:
    return {
        "fits": input_tokens < 10000,
        "input_tokens": input_tokens,
        "context_window": 10000,
        "output_tokens": max(10000 - input_tokens, 0),
        "token_budget_remaining": remaining,
        "token_budget_limit": 10000,
        "token_budget_measure": measure,
    }


def test_handoff_appends_request_and_response_and_next_run_uses_sourced_summary() -> None:
    first: dict[str, Any] = {}

    def pressure(options: dict[str, Any]) -> dict[str, Any]:
        has_assistant = any(item["role"] == "assistant" for item in options["messages"])
        # Large action-tool definitions trigger a handoff, even though its own
        # text-only summary request has a much smaller input estimate.
        return resource_view(8500 if has_assistant and options["tools"] else 1000)

    requests, _ = execute(
        [finish(), {"content": "verified result, unresolved next task"}],
        pressure=pressure,
        trace=first,
    )
    result = first["result"]
    assert len(requests) == 2
    assert requests[1]["messages"][: len(requests[0]["messages"])] == requests[0]["messages"]
    assert requests[1]["messages"][-2]["role"] == "tool"
    assert "NEXT run" in requests[1]["messages"][-1]["content"]
    assert result["messages"][:-1] == requests[1]["messages"]
    assert result["messages"][-1]["content"] == "verified result, unresolved next task"
    assert requests[1]["max_tokens"] == 1000
    continuation = result["continuation"]
    assert continuation["source_run_id"] == "run-1"
    assert "run-1" in continuation["messages"][0]["content"]
    assert first["marked"] == [{"mail_ids": ["mail"]}]
    resumed, _ = execute(
        [finish()],
        previous={"run_id": "run-1", "contract": "contract", "result": result},
        run_id="run-2",
    )
    assert resumed[0]["messages"][0] == continuation["messages"][0]
    assert resumed[0]["messages"][0] != result["messages"][0]


@pytest.mark.parametrize("measure,expected", [("output", 8000), ("total", 3000)])
def test_resource_policy_reserves_handoff_window_and_input_budget(
    measure: str, expected: int
) -> None:
    requests, _ = execute([finish()], pressure=lambda options: resource_view(measure=measure))
    assert requests[0]["max_tokens"] == expected


@pytest.mark.parametrize("input_tokens,remaining", [(9000, 10000), (1000, 1000)])
def test_insufficient_handoff_reserve_fails_before_model_or_effect(
    input_tokens: int, remaining: int
) -> None:
    from tlosure.eval import EvalError

    trace: dict[str, Any] = {}
    with pytest.raises(EvalError, match="preserving boundary handoff reserve"):
        execute(
            [finish()], pressure=lambda options: resource_view(input_tokens, remaining), trace=trace
        )
    assert trace["requests"] == trace["marked"] == trace["deliveries"] == trace["tool_calls"] == []


def test_failed_task_never_requests_boundary_handoff() -> None:
    from tlosure.eval import EvalError

    trace: dict[str, Any] = {}
    with pytest.raises(EvalError, match="finish-turn execution failed"):
        execute([finish()], succeeds=False, trace=trace)
    assert len(trace["requests"]) == 1
    assert trace["marked"] == []


@pytest.mark.parametrize(
    "response",
    [
        {"content": "partial", "finish_reason": "length"},
        {"content": ""},
        {"content": "summary", "tool_calls": [{"id": "bad", "name": "mail-send", "arguments": {}}]},
    ],
)
def test_bad_boundary_summary_never_marks_read_or_dispatches_summary_tools(
    response: dict[str, Any],
) -> None:
    from tlosure.eval import EvalError

    trace: dict[str, Any] = {}

    def pressure(options: dict[str, Any]) -> dict[str, Any]:
        return resource_view(
            8500
            if any(item["role"] == "assistant" for item in options["messages"]) and options["tools"]
            else 1000
        )

    with pytest.raises(EvalError, match="truncated|handoff requires"):
        execute([finish(), response], pressure=pressure, trace=trace)
    assert trace["marked"] == trace["deliveries"] == []
    assert [call["name"] for call in trace["tool_calls"]] == ["finish-turn"]
