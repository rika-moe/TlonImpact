"""Strict JSON encoding and pure assertions remain host independent."""

import json
from typing import Any

import pytest

from tlosure.builtins import CORE_BUILTINS, EvalError, builtin_json_encode
from tlosure.edn import Keyword, Symbol, loads
from tlosure.eval import eval_form


def test_json_encode_normalizes_keyword_keys_and_preserves_data() -> None:
    result = eval_form(
        loads('(json-encode {:data [true nil 3.5 "中文"] "code" "(mail-send {})"})'),
        {},
        CORE_BUILTINS,
    )
    assert json.loads(result) == {"data": [True, None, 3.5, "中文"], "code": "(mail-send {})"}


@pytest.mark.parametrize(
    "value",
    [
        Symbol("code"),
        Keyword("value"),
        float("nan"),
        float("inf"),
        {3: "value"},
        loads("(inc 1)"),
        {"x": object()},
    ],
)
def test_json_encode_rejects_non_json_values(value: Any) -> None:
    with pytest.raises(EvalError):
        builtin_json_encode({}, value)


def test_json_encode_rejects_cycles_and_unbounded_nesting() -> None:
    cycle: list[Any] = []
    cycle.append(cycle)
    with pytest.raises(EvalError, match="cyclic"):
        builtin_json_encode({}, cycle)
    nested: Any = None
    for _ in range(102):
        nested = [nested]
    with pytest.raises(EvalError, match="nesting"):
        builtin_json_encode({}, nested)


def test_assert_reports_program_validation_failure() -> None:
    assert eval_form(loads('(assert true "fine")'), {}, CORE_BUILTINS) is True
    with pytest.raises(EvalError, match="invalid data"):
        eval_form(loads('(assert false "invalid data")'), {}, CORE_BUILTINS)
    assert CORE_BUILTINS["assert"].pure and CORE_BUILTINS["json-encode"].pure


@pytest.mark.parametrize(
    ("literal", "expected"),
    [
        ("1", True),
        ("0", True),
        ("-2", True),
        ("1.0", False),
        ("true", False),
        ("false", False),
        ("nil", False),
        ('"1"', False),
    ],
)
def test_integer_predicate_excludes_booleans_and_floats(literal: str, expected: bool) -> None:
    assert eval_form(loads("(integer? " + literal + ")"), {}, CORE_BUILTINS) is expected
    assert CORE_BUILTINS["integer?"].pure
