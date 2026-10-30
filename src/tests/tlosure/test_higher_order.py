"""First-class collection functions share the evaluator's application boundary."""

import asyncio
from collections.abc import Generator
from typing import Any

import pytest

from tlosure.builtins import ARITHMETIC_BUILTINS, CORE_BUILTINS, Builtin, BuiltinCall
from tlosure.edn import loads
from tlosure.eval import EvalDepthError, EvalError, Evaluator, FuelExhaustedError, eval_form
from tlosure.validate import validate_form

VOCAB = CORE_BUILTINS | ARITHMETIC_BUILTINS


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("(let [f map] (f inc [1 2]))", [2, 3]),
        ("(reduce + 10 [1 2 3])", 16),
        ("(filter (fn [x] (> x 1)) [0 1 2])", [2]),
        ("(map (fn [xs] (map inc xs)) [[1] [2 3]])", [[2], [3, 4]]),
        ("(let [map (fn [x] (inc x))] (map 2))", 3),
        ("((if true inc dec) 4)", 5),
        ("(let [pick (fn [] map)] ((pick) inc [1]))", [2]),
        ("(map inc [])", []),
        ("(filter inc [])", []),
        ("(reduce + 7 [])", 7),
    ],
)
def test_first_class_functions(source: str, expected: Any) -> None:
    form = loads(source)
    validate_form(form, VOCAB, set())
    assert eval_form(form, {}, VOCAB) == expected


@pytest.mark.parametrize(
    "source",
    [
        "(map 1 [])",
        "(filter nil [])",
        "(reduce 1 0 [])",
        "(map inc nil)",
        '(filter inc "x")',
        "(reduce + 0 {})",
    ],
)
def test_empty_collection_still_checks_types(source: str) -> None:
    with pytest.raises(EvalError):
        eval_form(loads(source), {}, VOCAB)


def test_host_callbacks_consume_fuel_and_depth() -> None:
    form = loads("(map inc xs)")
    with pytest.raises(FuelExhaustedError):
        eval_form(form, {"xs": list(range(20))}, VOCAB, fuel=10)
    with pytest.raises(EvalDepthError):
        eval_form(loads("(map (fn [x] (map inc x)) [[1]])"), {}, VOCAB, max_depth=2)


@pytest.mark.asyncio
async def test_effect_alias_order_and_sync_async_fuel() -> None:
    def effect(env: dict[str, Any], value: int) -> int:
        return value + int(env["offset"])

    vocab = VOCAB | {"effect": Builtin(effect, pure=False)}
    form = loads("(let [f effect] (map f [1 2 3]))")
    sync = Evaluator(vocab, 100, 100)
    asynchronous = Evaluator(vocab, 100, 100)
    calls: list[tuple[str, list[Any]]] = []

    async def handler(call: BuiltinCall) -> Any:
        calls.append((call.name, call.args))
        return call.invoke()

    expected = sync.eval(form, {"offset": 10}, 0)
    assert (
        await asynchronous.eval_async(form, {"offset": 10}, 0, handler) == expected == [11, 12, 13]
    )
    assert calls == [("effect", [1]), ("effect", [2]), ("effect", [3])]
    assert asynchronous.fuel_remaining == sync.fuel_remaining


@pytest.mark.asyncio
async def test_cancel_closes_collection_generator_and_function_scope() -> None:
    from tlosure.builtins import FunctionCall

    closed: list[bool] = []

    def forwarding(env: dict[str, Any], target: Any) -> Generator[FunctionCall, Any, Any]:
        try:
            return (yield FunctionCall(target, []))
        finally:
            closed.append(True)

    vocab = VOCAB | {
        "forward": Builtin(forwarding),
        "effect": Builtin(lambda env: None, pure=False),
    }
    evaluator = Evaluator(vocab, 100, 100)

    async def cancel(call: BuiltinCall) -> Any:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await evaluator.eval_async(
            loads("(while [x 0] true (forward (fn [] (effect))))"), {}, 0, cancel
        )
    assert closed == [True]
    with pytest.raises(EvalError, match="recur outside while"):
        evaluator.eval(loads("(recur)"), {}, 0)
