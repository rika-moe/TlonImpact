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
            "tool-execute": Builtin(lambda env, options: [], pure=False),
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
            {"content": value}
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
