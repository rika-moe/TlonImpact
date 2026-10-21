"""Closed module packages preserve ordinary function composition and budgets."""

import pytest

from tlosure.builtins import ARITHMETIC_BUILTINS, CORE_BUILTINS, Builtin
from tlosure.edn import loads, loads_all
from tlosure.eval import EvalDepthError, FuelExhaustedError, eval_form
from tlosure.modules import ModuleError, ModulePackage

BUILTINS = {**CORE_BUILTINS, **ARITHMETIC_BUILTINS}


def test_loads_all_preserves_single_form_parser() -> None:
    assert loads_all("; comment\n 1 (inc 2) ; tail") == [1, loads("(inc 2)")]
    assert loads_all("; empty") == []


def test_imports_private_helpers_forward_recursion_and_higher_order_calls() -> None:
    package = ModulePackage.from_sources(
        {
            "work/reply.tlon": """
            (ns work.reply (:require [work.math :as math]) (:export handle))
            (defn handle [event] (apply-one math/factorial event))
            (defn apply-one [f value] (f value))
            """,
            "work/math.tlon": """
            (ns work.math (:export factorial))
            (def unit 1)
            (defn factorial [n] (if (< n 2) unit (* n (factorial (- n 1)))))
            (defn private-helper [x] x)
            """,
        },
        BUILTINS,
    )
    closure = package.entry("work.reply/handle")
    assert eval_form(loads("(entry 5)"), {"entry": closure}, BUILTINS) == 120
    assert set(package.exports) == {"work.reply/handle", "work.math/factorial"}
    assert "math/private-helper" not in closure.env
    with pytest.raises(FuelExhaustedError):
        eval_form(loads("(entry 5)"), {"entry": closure}, BUILTINS, fuel=10)
    with pytest.raises(EvalDepthError):
        eval_form(loads("(entry 5)"), {"entry": closure}, BUILTINS, max_depth=4)


def test_loading_does_not_execute_host_functions() -> None:
    calls: list[int] = []
    builtins = {"host": Builtin(lambda env, x: calls.append(x) or x, pure=False)}
    package = ModulePackage.from_sources(
        {"main.tlon": "(ns main (:export run)) (defn run [x] (host x))"}, builtins
    )
    assert calls == []
    assert eval_form(loads("(entry 3)"), {"entry": package.entry("main/run")}, builtins) == 3
    assert calls == [3]


@pytest.mark.parametrize(
    "source",
    [
        "(ns main) (host 1)",
        "(ns main) (def value (host 1))",
        "(ns main) (def value [unknown])",
        "(ns main) (def value 1) (def value 2)",
        "(ns main) (defn run [x] (missing x))",
        "(ns main (:export missing))",
        "(ns main (:export value)) (def value 1)",
        "(ns main (:export run run)) (defn run [x] x)",
        "(ns main (:export) (:export))",
        "(ns main (:unknown))",
        "(ns main (:require [other :as x] [other :as x]))",
        "(ns main) (defn if [x] x)",
        "(ns wrong)",
        "(ns main) (defn run [x])",
        "(ns main) (defn run [x x] x)",
        "(ns main) (defn run [x & x] x)",
        "(:ns main)",
        '(ns main (:require [other "as" alias]))',
        "(ns main",
    ],
)
def test_rejects_invalid_declarations_and_load_effects(source: str) -> None:
    with pytest.raises(ModuleError):
        ModulePackage.from_sources({"main.tlon": source}, BUILTINS)


@pytest.mark.parametrize(
    "path", ["../main.tlon", "/main.tlon", "a//b.tlon", "a.b.tlon", "a\\b.tlon", "main.edn"]
)
def test_rejects_noncanonical_source_paths(path: str) -> None:
    with pytest.raises(ModuleError):
        ModulePackage.from_sources({path: "(ns main)"}, BUILTINS)


def test_rejects_cycles_unknown_dependencies_and_private_access() -> None:
    with pytest.raises(ModuleError, match="cycle"):
        ModulePackage.from_sources(
            {"a.tlon": "(ns a (:require [b :as b]))", "b.tlon": "(ns b (:require [a :as a]))"},
            BUILTINS,
        )
    with pytest.raises(ModuleError, match="unknown module"):
        ModulePackage.from_sources({"a.tlon": "(ns a (:require [b :as b]))"}, BUILTINS)
    with pytest.raises(ModuleError, match="unknown function"):
        ModulePackage.from_sources(
            {
                "a.tlon": "(ns a (:require [b :as b])) (defn run [x] (b/private x))",
                "b.tlon": "(ns b) (defn private [x] x)",
            },
            BUILTINS,
        )


def test_entry_requires_exported_fixed_single_argument() -> None:
    package = ModulePackage.from_sources(
        {"main.tlon": "(ns main (:export zero many)) (defn zero [] 1) (defn many [x & more] x)"},
        BUILTINS,
    )
    for name in ["main/missing", "main/zero", "main/many"]:
        with pytest.raises(ModuleError):
            package.entry(name)
