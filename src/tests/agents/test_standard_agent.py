"""Ordinary delivery policy reviews before sending and trusts committed receipts."""

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


def test_review_occurs_before_actual_delivery_without_duplicate_reply() -> None:
    requests, deliveries = execute(
        [
            call(),
            {"content": "reviewed delivery"},
            {"content": "summary"},
            {"content": "reviewed summary"},
        ]
    )
    assert len(requests) == 4
    assert len(deliveries) == 1
    assert deliveries[0]["content"] == "reviewed delivery"
    assert "BEFORE it is sent" in requests[1]["messages"][-1]["content"]
    assert "known fact" in requests[0]["messages"][0]["content"]


def test_failed_delivery_does_not_suppress_automatic_reply() -> None:
    _, deliveries = execute(
        [
            call(),
            {"content": "reviewed delivery"},
            {"content": "report failed delivery"},
            {"content": "honest reply"},
        ],
        succeeds=False,
    )
    assert len(deliveries) == 1
    assert deliveries[0]["content"] == "honest reply"


def test_empty_review_cannot_deliver_unreviewed_mail() -> None:
    from tlosure.eval import EvalError

    with pytest.raises(EvalError, match="review requires"):
        execute([call(), {"content": ""}])


def test_peer_delivery_preserves_user_reply() -> None:
    response = call()
    response["tool_calls"][0]["arguments"]["to"] = ["peer@tlon.internal"]
    _, deliveries = execute(
        [response, {"content": "peer delivery"}, {"content": "answer"}, {"content": "user reply"}]
    )
    assert [delivery["content"] for delivery in deliveries] == ["peer delivery", "user reply"]


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


@pytest.mark.parametrize(
    "sender,receipt,expected",
    [
        ("alice@external.com", {"local_recipients": ["alice"], "gateways": []}, False),
        (
            "alice@external.com",
            {
                "local_recipients": [],
                "gateways": [{"address": "alice@external.com", "status": "failed"}],
            },
            False,
        ),
        (
            "alice@external.com",
            {
                "local_recipients": [],
                "gateways": [{"address": "alice@external.com", "status": "delivered"}],
            },
            True,
        ),
        ("alice@tlon.internal", {"local_recipients": ["alice"], "gateways": []}, True),
        ("alice@tlon.internal", {"local_recipients": ["bob"], "gateways": []}, False),
    ],
)
def test_delivery_detection_requires_exact_recipient_receipt(
    sender: str,
    receipt: dict[str, Any],
    expected: bool,
) -> None:
    from importlib.resources import files

    from tests.agents.test_workflow_library import edn

    builtins = program_builtins()
    source = files("tlon").joinpath("resources/stdlib/agent.tlon").read_text()
    # Test the private ordinary helper through a same-module public wrapper.
    source = source.replace("(:export respond)", "(:export respond receipt-check)")
    source += (
        "\n(defn receipt-check [event] (replied? (get event :exchanges) (get event :sender)))\n"
    )
    sources = program_module_sources(
        {
            "test.tlon": "(ns test (:require [tlon.agent :as agent]) (:export handle)) (defn handle [event] (agent/receipt-check event))"
        }
    )
    sources["tlon/agent.tlon"] = source
    package = ModulePackage.from_sources(sources, builtins)
    # CC delivery is proven by local_recipients even though original :to excludes alice.
    event = {
        "sender": sender,
        "exchanges": [
            {
                "calls": [
                    {
                        "id": "send",
                        "name": "mail-send",
                        "arguments": {"to": ["bob@tlon.internal"], "cc": [sender]},
                    }
                ],
                "results": [{"id": "send", "name": "mail-send", "success": True, "data": receipt}],
            }
        ],
    }
    result = eval_form(
        loads("(handle " + edn(event) + ")"),
        {"handle": package.entry("test/handle")},
        builtins,
        fuel=1000,
    )
    assert result is expected


def test_plan_is_computed_once_before_tool_execution() -> None:
    requests, deliveries = execute(
        [
            {"content": "plan"},
            call(),
            {"content": "reviewed delivery"},
            {"content": "answer"},
            {"content": "reviewed answer"},
        ],
        method="plan-then-act",
    )
    assert len(requests) == 5
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
