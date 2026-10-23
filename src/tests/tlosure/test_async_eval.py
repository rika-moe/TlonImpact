"""The sync and async drivers share expression semantics and fuel accounting."""

import asyncio
from typing import Any

import pytest

from tlosure.builtins import (
    ARITHMETIC_BUILTINS,
    CORE_BUILTINS,
    Builtin,
    BuiltinCall,
    Closure,
    EvalError,
    FuelExhaustedError,
)
from tlosure.edn import loads
from tlosure.eval import Evaluator


def evaluator(*, extra: dict[str, Builtin] | None = None, fuel: int = 1000) -> Evaluator:
    return Evaluator({**CORE_BUILTINS, **ARITHMETIC_BUILTINS, **(extra or {})}, fuel, 100)


@pytest.mark.parametrize(
    "source",
    [
        "(let [x 2 y (+ x 3)] {:sum (+ x y) :values [x y]})",
        "(while [n 0] (< n 4) (if (= n 3) n (recur (inc n))))",
        "(let [f (fn [x & rest] (+ x (count rest)))] (map n (f n 7 8) [1 2]))",
        "(let [unused (defn sum [n] (if (= n 0) 0 (+ n (sum (dec n)))))] (sum 4))",
        "(reduce a x (+ a x) 0 (filter n (> n 1) [1 2 3]))",
        "(quote {:data (unknown effect)})",
    ],
)
async def test_pure_drivers_have_equal_values_and_fuel(source: str) -> None:
    sync, asynchronous = evaluator(), evaluator()

    async def forbidden(call: BuiltinCall) -> Any:
        pytest.fail(f"pure program invoked handler: {call.name}")

    form = loads(source)
    assert await asynchronous.eval_async(form, {}, 0, forbidden) == sync.eval(form, {}, 0)
    assert asynchronous.fuel_remaining == sync.fuel_remaining


async def test_handler_can_return_recorded_result_without_invoking_builtin() -> None:
    def forbidden(env: dict[str, Any], value: Any) -> Any:
        pytest.fail("the original impure function must remain uncalled on replay")

    calls: list[tuple[str, list[Any]]] = []

    async def replay(call: BuiltinCall) -> Any:
        calls.append((call.name, call.args))
        return 41

    run = evaluator(extra={"fetch": Builtin(forbidden, pure=False)})
    assert await run.eval_async(loads('(let [x (fetch "key")] (+ x 1))'), {}, 0, replay) == 42
    assert calls == [("fetch", ["key"])]


async def test_effect_results_are_values_not_recursively_executed_actions() -> None:
    requests: list[BuiltinCall] = []
    result = {"kind": "saved", "data": {}}

    async def handler(call: BuiltinCall) -> Any:
        requests.append(call)
        return result

    run = evaluator(extra={"effect": Builtin(lambda env: None, pure=False)})
    value = await run.eval_async(loads("(let [x (effect)] [x x (quote (effect))])"), {}, 0, handler)
    assert value[:2] == [result, result]
    assert value[2] == loads("(effect)")
    assert len(requests) == 1


async def test_short_circuit_skips_effects() -> None:
    calls: list[int] = []

    async def handler(call: BuiltinCall) -> Any:
        calls.append(call.args[0])
        return call.args[0]

    run = evaluator(extra={"fetch": Builtin(lambda env, x: x, pure=False)})
    form = loads("[(if false (fetch 1) (fetch 2)) (and false (fetch 3)) (or true (fetch 4))]")
    assert await run.eval_async(form, {}, 0, handler) == [2, False, True]
    assert calls == [2]


async def test_nested_closures_map_and_recur_resume_in_order() -> None:
    calls: list[int] = []

    async def handler(call: BuiltinCall) -> Any:
        calls.append(call.args[0])
        return call.invoke() * 10

    run = evaluator(extra={"fetch": Builtin(lambda env, x: x, pure=False)})
    source = """(let [f (fn [x] (fetch x))]
      (map n (while [i 0 total 0] true
        (if (= i 2) total (recur (inc i) (+ total (f (+ n i)))))) [1 3]))"""
    assert await run.eval_async(loads(source), {}, 0, handler) == [30, 70]
    assert calls == [1, 2, 3, 4]


async def test_impure_closure_registry_entry_still_runs_inside_budget() -> None:
    closure = Closure(params=["x"], rest=None, body=[loads("(fetch (+ x 1))")], env={})
    run = evaluator(
        extra={
            "fetch": Builtin(lambda env, x: x, pure=False),
            "word": Builtin.from_closure(closure, pure=False),
        }
    )
    calls: list[str] = []

    async def handler(call: BuiltinCall) -> Any:
        calls.append(call.name)
        return call.invoke()

    assert await run.eval_async(loads("(word 3)"), {}, 0, handler) == 4
    assert calls == ["fetch"]
    assert run.fuel_remaining == 994


async def test_cancellation_closes_function_and_loop_frames() -> None:
    entered = asyncio.Event()

    async def suspended(call: BuiltinCall) -> Any:
        entered.set()
        await asyncio.Future()

    run = evaluator(extra={"pause": Builtin(lambda env: None, pure=False)})
    source = "(while [n 0] true ((fn [] (while [m 0] true (pause)))))"
    task = asyncio.create_task(run.eval_async(loads(source), {}, 0, suspended))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert run._loop_stack == []
    with pytest.raises(EvalError, match="recur outside while"):
        run.eval(loads("(recur)"), {}, 0)


async def test_handler_errors_keep_original_types_and_objects() -> None:
    run = evaluator(extra={"fail": Builtin(lambda env: None, pure=False)})
    expected = EvalError("cannot run")

    async def fail(call: BuiltinCall) -> Any:
        raise expected

    with pytest.raises(EvalError) as caught:
        await run.eval_async(loads("(fail)"), {}, 0, fail)
    assert caught.value is expected

    unavailable = OSError("offline")

    async def crashed(call: BuiltinCall) -> Any:
        raise unavailable

    with pytest.raises(OSError, match="offline") as caught:
        await run.eval_async(loads("(fail)"), {}, 0, crashed)
    assert caught.value is unavailable


async def test_fuel_exhaustion_matches_sync_without_extra_host_calls() -> None:
    form = loads("(while [n 0] true (recur (inc n)))")
    sync, asynchronous = evaluator(fuel=30), evaluator(fuel=30)

    async def forbidden(call: BuiltinCall) -> Any:
        pytest.fail("no host calls expected")

    with pytest.raises(FuelExhaustedError):
        sync.eval(form, {}, 0)
    with pytest.raises(FuelExhaustedError):
        await asynchronous.eval_async(form, {}, 0, forbidden)
    assert sync.fuel_remaining == asynchronous.fuel_remaining == -1


async def test_pure_builtin_failure_is_an_expression_error() -> None:
    run = evaluator(extra={"explode": Builtin(lambda env: 1 / 0, pure=True)})

    async def forbidden(call: BuiltinCall) -> Any:
        pytest.fail("pure arithmetic must not call the host handler")

    with pytest.raises(EvalError, match="division by zero") as caught:
        await run.eval_async(loads("(explode)"), {}, 0, forbidden)
    assert isinstance(caught.value.__cause__, ZeroDivisionError)
