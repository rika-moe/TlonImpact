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


def test_json_shared_subtrees_count_each_expansion() -> None:
    shared = [1, 2]
    assert builtin_json_encode({}, [shared, shared]) == "[[1,2],[1,2]]"
    tree: Any = None
    for _ in range(20):
        tree = [tree, tree]
    with pytest.raises(EvalError, match="expansion"):
        builtin_json_encode({}, tree)


def test_json_node_boundary() -> None:
    assert len(json.loads(builtin_json_encode({}, [0] * 99_999))) == 99_999
    with pytest.raises(EvalError, match="expansion"):
        builtin_json_encode({}, [0] * 100_000)


@pytest.mark.parametrize("text", ["a", "中", "😀", "\\", '"', "\n", "\x00"])
def test_json_exact_utf8_output_boundary(text: str, monkeypatch: pytest.MonkeyPatch) -> None:
    value = {text: [text, True, False, None, -123, 1e100]}
    expected = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    size = len(expected.encode("utf-8"))
    monkeypatch.setattr("tlosure.builtins.JSON_MAX_BYTES", size)
    assert builtin_json_encode({}, value) == expected
    monkeypatch.setattr("tlosure.builtins.JSON_MAX_BYTES", size - 1)
    with pytest.raises(EvalError, match="UTF-8 bytes"):
        builtin_json_encode({}, value)


def test_json_large_scalar_limits() -> None:
    text = "a" * (1_048_576 - 2)
    assert len(builtin_json_encode({}, text)) == 1_048_576
    with pytest.raises(EvalError, match="UTF-8 bytes"):
        builtin_json_encode({}, text + "a")
    with pytest.raises(EvalError, match="UTF-8 bytes"):
        builtin_json_encode({}, 1 << 4_000_000)
    with pytest.raises(EvalError, match="decimal conversion"):
        builtin_json_encode({}, 10**5000)


@pytest.mark.parametrize("value", ["\ud800", {"\udfff": 1}])
def test_json_rejects_non_utf8_strings(value: Any) -> None:
    with pytest.raises(EvalError, match="Unicode scalar"):
        builtin_json_encode({}, value)


def test_json_depth_boundary() -> None:
    value: Any = 0
    for _ in range(100):
        value = [value]
    assert json.loads(builtin_json_encode({}, value)) == value
    with pytest.raises(EvalError, match="nesting"):
        builtin_json_encode({}, [value])


@pytest.mark.parametrize("levels", [4, 6, 8])
def test_json_dsl_shared_expansion_budget(levels: int, monkeypatch: pytest.MonkeyPatch) -> None:
    bindings = " ".join(f"v{i} [v{i - 1} v{i - 1}]" for i in range(1, levels + 1))
    form = loads(f"(let [v0 nil {bindings}] (json-encode v{levels}))")
    node_count = 2 ** (levels + 1) - 1
    monkeypatch.setattr("tlosure.builtins.JSON_MAX_NODES", node_count)
    result = eval_form(form, {}, CORE_BUILTINS)
    assert result.count("null") == 2**levels
    monkeypatch.setattr("tlosure.builtins.JSON_MAX_NODES", node_count - 1)
    with pytest.raises(EvalError, match="expansion"):
        eval_form(form, {}, CORE_BUILTINS)
