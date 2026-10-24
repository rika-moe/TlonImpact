"""Action loops deliver and finish only through successfully executed tools."""

from typing import Any

import pytest

from tlon.agents.program_package import program_builtins, program_module_sources
from tlon.operations.schema import edn_value
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
            "model-context": Builtin(lambda env, options: {"fits": True}, pure=False),
            "tool-execute": Builtin(tools, pure=False),
            "mail-send": Builtin(send, pure=False),
            "mail-mark-read": Builtin(lambda env, options: marked.append(options), pure=False),
            "member-context": Builtin(
                lambda env, options: {
                    "knowledge": "known fact",
                    "lessons": "lesson",
                    "skills": "skill",
                    "current_task": "task",
                    "thread_recap": "recap",
                    "mail_history": [],
                    "tools": ["mail-send", "finish-turn"],
                },
                pure=False,
            ),
        }
    )
    sources = program_module_sources(
        {
            "test.tlon": """
      (ns test (:require [tlon.agent :as agent]) (:export handle))
      (defn handle [event]
        (agent/respond event {:method (get event :method) :max_tokens 512 :max_rounds (get event :max_rounds)}))
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
    eval_form(
        loads("(handle event)"),
        {"handle": package.entry("test/handle"), "event": event},
        builtins,
        fuel=10000,
    )
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


def test_review_occurs_before_delivery_and_sending_does_not_finish() -> None:
    trace: dict[str, Any] = {}
    requests, deliveries = execute(
        [call(), {"content": "reviewed delivery"}, finish()], trace=trace
    )
    assert len(requests) == 3
    assert [delivery["content"] for delivery in deliveries] == ["reviewed delivery"]
    assert "BEFORE it is sent" in requests[1]["messages"][-1]["content"]
    assert "known fact" in requests[0]["messages"][0]["content"]
    assert [item["name"] for item in trace["tool_calls"]] == ["mail-send", "finish-turn"]
    assert trace["marked"] == [{"mail_ids": ["mail"]}]


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


@pytest.mark.parametrize("phase", ["reviewed", "plan-then-act"])
def test_truncated_internal_text_cannot_trigger_actions(phase: str) -> None:
    from tlosure.eval import EvalError

    trace: dict[str, Any] = {}
    responses = [{"content": "partial", "finish_reason": "length"}]
    if phase == "reviewed":
        responses.insert(0, call())
    with pytest.raises(EvalError, match="model output truncated"):
        execute(responses, method=phase, trace=trace)
    assert trace["marked"] == trace["deliveries"] == trace["tool_calls"] == []


def test_empty_review_cannot_deliver_unreviewed_mail() -> None:
    from tlosure.eval import EvalError

    with pytest.raises(EvalError, match="review requires"):
        execute([call(), {"content": ""}])


def test_peer_delivery_does_not_create_an_automatic_reply() -> None:
    response = call()
    response["tool_calls"][0]["arguments"]["to"] = ["peer@tlon.internal"]
    _, deliveries = execute([response, {"content": "peer delivery"}, finish()])
    assert [delivery["content"] for delivery in deliveries] == ["peer delivery"]


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
            {"content": "reviewed delivery"},
            finish(),
        ],
        method="plan-then-act",
    )
    assert len(requests) == 4
    assert requests[0]["tools"] == []
    assert "Plan at most five steps" in requests[0]["messages"][-1]["content"]
    assert "Execute the plan" in requests[1]["messages"][-1]["content"]
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
