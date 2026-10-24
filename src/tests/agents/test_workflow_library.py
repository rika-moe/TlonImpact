"""Ordinary source functions preserve tool evidence and nested return boundaries."""

from importlib.resources import files
from typing import Any

import pytest

from tlosure.builtins import ARITHMETIC_BUILTINS, CORE_BUILTINS, Builtin
from tlosure.edn import Keyword, dumps, loads
from tlosure.eval import EvalError, eval_form
from tlosure.modules import ModulePackage


def run(
    expression: str, responses: list[dict[str, Any]] | None = None, window: int | None = None
) -> tuple[Any, list[Any]]:
    requests: list[Any] = []

    def model(env: dict[str, Any], options: Any) -> Any:
        requests.append(options)
        assert responses is not None
        return responses.pop(0)

    builtins = {**CORE_BUILTINS, **ARITHMETIC_BUILTINS}
    builtins.update(
        {
            "model-call": Builtin(model, pure=False),
            "model-context": Builtin(
                lambda env, options: {"fits": window is None or len(str(options)) < window},
                pure=False,
            ),
            "tool-execute": Builtin(
                lambda env, options: [
                    {
                        "id": call["id"],
                        "name": call["name"],
                        "success": True,
                        "data": {"finished": True},
                        "error": None,
                    }
                    for call in options["tool_calls"]
                ],
                pure=False,
            ),
            "mail-send": Builtin(lambda env, options: options, pure=False),
            "mail-forward": Builtin(lambda env, options: options, pure=False),
        }
    )
    library = files("tlon").joinpath("resources", "stdlib")
    sources = {
        f"tlon/{name}.tlon": library.joinpath(f"{name}.tlon").read_text()
        for name in ["messages", "context", "conversation", "workflows"]
    }
    sources["work.tlon"] = (
        "(ns work (:require [tlon.context :as context] [tlon.workflows :as workflows]) "
        "(:export handle)) (defn handle [event] " + expression + ")"
    )
    package = ModulePackage.from_sources(sources, builtins)
    return eval_form(
        loads("(handle {})"), {"handle": package.entry("work/handle")}, builtins, fuel=10000
    ), requests


def edn(value: Any) -> str:
    def convert(item: Any) -> Any:
        if isinstance(item, dict):
            return {Keyword(key): convert(child) for key, child in item.items()}
        if isinstance(item, list):
            return [convert(child) for child in item]
        return item

    return dumps(convert(value))


def test_complete_tool_groups_and_unexecuted_proposal_are_distinct() -> None:
    history = [
        {"role": "user", "content": "fixed prefix"},
        {"role": "assistant", "content": "draft; no tool execution"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "lookup", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": '{"success":true,"data":"source fact"}'},
        {"role": "user", "content": "latest"},
    ]
    result, requests = run(
        "(context/compact {:messages "
        + edn(history)
        + " :cache_prefix_len 1 :max_tokens 40 :recent_groups 1})",
        [{"content": "draft unexecuted"}, {"content": "source fact; draft unexecuted"}],
    )
    assert result["messages"][0] == history[0]
    assert result["messages"][-1] == history[-1]
    assert result["cache_prefix_len"] == 1
    assert len(requests) == 2 and requests[0]["tools"] == []
    assert "UNEXECUTED" in requests[0]["messages"][0]["content"]
    assert "source fact" in requests[1]["messages"][0]["content"]
    assert "draft; no tool execution" in requests[0]["messages"][0]["content"]
    assert result["messages"][1]["content"].startswith(
        "Historical summary (not a new execution receipt)"
    )


@pytest.mark.parametrize(
    "history",
    [
        [{"role": "tool", "tool_call_id": "x", "content": "orphan"}],
        [{"role": "assistant", "tool_calls": [{"id": "x"}]}],
        [
            {"role": "assistant", "tool_calls": [{"id": "x"}]},
            {"role": "tool", "tool_call_id": "wrong", "content": "bad"},
        ],
    ],
)
def test_compaction_rejects_incomplete_protocol(history: Any) -> None:
    with pytest.raises(EvalError):
        run("(context/groups " + edn(history) + ")")


def test_prefix_cannot_split_tool_exchange() -> None:
    history = [
        {"role": "assistant", "tool_calls": [{"id": "x"}]},
        {"role": "tool", "tool_call_id": "x", "content": "fact"},
        {"role": "user", "content": "latest"},
    ]
    with pytest.raises(EvalError, match="prefix splits"):
        run("(context/compact {:messages " + edn(history) + " :prefix_count 1 :max_tokens 10})")


def test_two_nested_methods_return_before_outer_continues() -> None:
    result, requests = run(
        '(let [options {:messages [{:role "user" :content "verify"}] :tools [] :max_tokens 32 :max_rounds 2} '
        "checked (workflows/evidence-check options) "
        "reviewed (workflows/reviewed options)] "
        '{:checked (get checked :content) :reviewed (get reviewed :content) :outer "continued"})',
        [
            {
                "content": value,
                "tool_calls": [{"id": "done", "name": "finish-turn", "arguments": {}}],
            }
            if value in {"source A", "source B", "draft"}
            else {"content": value}
            for value in ["source A", "source B", "cross check", "draft", "review", "revision"]
        ],
    )
    assert result == {"checked": "cross check", "reviewed": "revision", "outer": "continued"}
    assert len(requests) == 6
    assert "source A" in requests[2]["messages"][0]["content"]
    assert "source B" in requests[2]["messages"][0]["content"]


def test_pressure_compacts_incrementally_when_batch_cannot_fit() -> None:
    history = [{"role": "user", "content": "x" * 200} for _ in range(4)]
    history.append({"role": "user", "content": "latest"})
    result, requests = run(
        "(context/fit {:messages " + edn(history) + " :max_tokens 32})",
        [{"content": "brief source summary"} for _ in range(4)],
        window=800,
    )
    assert len(requests) == 4
    assert result["messages"][-1] == history[-1]
    assert len(result["messages"]) == 2


def test_single_oversized_exchange_does_not_call_provider() -> None:
    history = [{"role": "user", "content": "x" * 2000}, {"role": "user", "content": "latest"}]
    # No mock responses: any provider dispatch would fail instead of this explicit rejection.
    with pytest.raises(EvalError, match="single exchange"):
        run("(context/fit {:messages " + edn(history) + " :max_tokens 32})", window=800)


def test_forward_uses_original_mail_identity() -> None:
    result, requests = run('(workflows/forward "reader@tlon.internal" "mail-source")')
    assert result == {"to": ["reader@tlon.internal"], "mail_id": "mail-source"}
    assert requests == []


def test_omitted_output_limit_stays_absent_from_model_request() -> None:
    result, requests = run(
        '(context/complete {:messages [{:role "user" :content "plan"}] :tools []})',
        [{"content": "plan"}],
    )
    assert result["content"] == "plan"
    assert "max_tokens" not in requests[0]


def test_compaction_rejects_truncated_summary() -> None:
    history = [{"role": "user", "content": value} for value in ["old", "recent"]]
    with pytest.raises(EvalError, match="model output truncated"):
        run(
            "(context/compact {:messages " + edn(history) + "})",
            [{"content": "partial", "finish_reason": "length"}],
        )


def test_short_turn_allows_delivery_followed_by_explicit_finish() -> None:
    result, requests = run(
        '(workflows/short-turn {:messages [] :tools ["mail-send" "finish-turn"]})',
        [
            {
                "tool_calls": [
                    {
                        "id": "send",
                        "name": "mail-send",
                        "arguments": {"to": ["sender@tlon.internal"], "content": "answer"},
                    }
                ]
            },
            {"tool_calls": [{"id": "done", "name": "finish-turn", "arguments": {}}]},
        ],
    )
    assert result["reason"] == "finish-turn"
    assert len(requests) == 2
    assert requests[1]["messages"][-1]["tool_call_id"] == "send"


def test_standard_work_example_composes_methods_then_delivers_with_tools() -> None:
    from tlon.agents.program_package import program_builtins, program_module_sources

    example = files("tlon").joinpath("resources/examples/standard-work")
    responses = [
        {"content": "plan"},
        {"tool_calls": [{"id": "clock", "name": "time-now", "arguments": {}}]},
        {"tool_calls": [{"id": "planned", "name": "finish-turn", "arguments": {}}]},
        {"tool_calls": [{"id": "drafted", "name": "finish-turn", "arguments": {}}]},
        {"content": "review"},
        {"content": "revised answer"},
        {
            "tool_calls": [
                {
                    "id": "send",
                    "name": "mail-send",
                    "arguments": {"to": ["sender@tlon.internal"], "content": "revised answer"},
                }
            ]
        },
        {"tool_calls": [{"id": "delivered", "name": "finish-turn", "arguments": {}}]},
    ]
    requests: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    marked: list[dict[str, Any]] = []

    def model(env: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
        requests.append(request)
        return responses.pop(0)

    def execute(env: dict[str, Any], request: dict[str, Any]) -> list[dict[str, Any]]:
        calls.extend(request["tool_calls"])
        return [
            {"id": call["id"], "name": call["name"], "success": True, "data": {}, "error": None}
            for call in request["tool_calls"]
        ]

    builtins = program_builtins()
    builtins.update(
        {
            "model-call": Builtin(model, pure=False),
            "model-context": Builtin(lambda env, options: {"fits": True}, pure=False),
            "tool-execute": Builtin(execute, pure=False),
            "mail-mark-read": Builtin(lambda env, options: marked.append(options), pure=False),
        }
    )
    sources = program_module_sources(
        {
            f"work/{name}.tlon": example.joinpath(f"modules/work/{name}.tlon").read_text()
            for name in ["methods", "reply"]
        }
    )
    package = ModulePackage.from_sources(sources, builtins)
    result = eval_form(
        loads(
            '(handle {:sender "sender@tlon.internal" :mail {:id "incoming" :content "What time is it?"}})'
        ),
        {"handle": package.entry("work.reply/handle")},
        builtins,
        fuel=10000,
    )
    assert result["result"]["reason"] == "finish-turn"
    assert len(requests) == 8
    assert all("max_tokens" not in request for request in requests)
    assert [call["name"] for call in calls] == [
        "time-now",
        "finish-turn",
        "finish-turn",
        "mail-send",
        "finish-turn",
    ]
    assert calls[-2]["arguments"]["content"] == "revised answer"
    assert "revised answer" in requests[-2]["messages"][-1]["content"]
    assert marked == [{"mail_ids": ["incoming"]}]
