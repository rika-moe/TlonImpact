"""Direct unit tests for the general vocabulary (``tlosure.builtins``).

``test_eval.py`` exercises the builtins through the evaluator; this suite
calls ``Builtin.fn(env, *args)`` directly to pin each function's full
contract — the totality of ``get``, the type-distinct equality of ``=``,
the ``_num`` coercion rules (bool is rejected even though it is an int
subclass), and the frozen dataclass contracts of :class:`Builtin` and
:class:`ActionRequest`.
"""

import dataclasses
from typing import Any

import pytest

from tlosure.builtins import (
    ARITHMETIC_BUILTINS,
    CORE_BUILTINS,
    ActionRequest,
    Builtin,
    EvalDepthError,
    EvalError,
    FuelExhaustedError,
)
from tlosure.edn import Keyword, Symbol

_ENV: dict[str, Any] = {}


def _call(name: str, *args: Any) -> Any:
    """Invoke a general-vocabulary builtin by name with an empty env."""
    builtins = {**CORE_BUILTINS, **ARITHMETIC_BUILTINS}
    return builtins[name].fn(_ENV, *args)


class TestContractTypes:
    def test_builtin_is_frozen_and_pure_by_default(self):
        builtin = Builtin(fn=lambda env: None)
        assert builtin.pure is True
        with pytest.raises(dataclasses.FrozenInstanceError):
            builtin.pure = False  # type: ignore[misc]

    def test_builtin_impure_marker(self):
        assert Builtin(fn=lambda env: None, pure=False).pure is False

    def test_action_request_is_frozen(self):
        request = ActionRequest(kind="send", data={"to": "x"})
        assert request.kind == "send"
        assert request.data == {"to": "x"}
        with pytest.raises(dataclasses.FrozenInstanceError):
            request.kind = "other"  # type: ignore[misc]

    def test_error_hierarchy(self):
        assert issubclass(FuelExhaustedError, EvalError)
        assert issubclass(EvalDepthError, EvalError)

    def test_fuel_exhausted_carries_budget(self):
        err = FuelExhaustedError("fuel exhausted", budget=42)
        assert err.budget == 42
        assert str(err) == "fuel exhausted"


class TestRegistries:
    def test_core_vocabulary_names(self):
        assert set(CORE_BUILTINS) == {
            "=",
            "not",
            "contains?",
            "get",
            "str",
            "split",
            "join",
            "substring",
        }

    def test_arithmetic_vocabulary_names(self):
        assert set(ARITHMETIC_BUILTINS) == {"+", "-", "*", "/", "inc", "dec", "<", ">", "<=", ">="}

    def test_general_vocabulary_is_pure(self):
        for registry in (CORE_BUILTINS, ARITHMETIC_BUILTINS):
            for name, builtin in registry.items():
                assert builtin.pure, f"{name} must be pure"


class TestEq:
    def test_fewer_than_two_args_is_true(self):
        assert _call("=") is True
        assert _call("=", 1) is True

    def test_variadic_equality(self):
        assert _call("=", 1, 1, 1) is True
        assert _call("=", 1, 1, 2) is False

    def test_string_types_stay_distinct(self):
        # `:a` ≠ "a" ≠ a — Keyword/Symbol/str never compare equal.
        assert _call("=", Keyword("a"), "a") is False
        assert _call("=", "a", Symbol("a")) is False
        assert _call("=", Keyword("a"), Symbol("a")) is False
        assert _call("=", Keyword("a"), Keyword("a")) is True
        assert _call("=", Symbol("a"), Symbol("a")) is True
        assert _call("=", "a", "a") is True

    def test_non_strings_use_plain_equality(self):
        assert _call("=", [1, 2], [1, 2]) is True
        assert _call("=", {"a": 1}, {"a": 1}) is True
        assert _call("=", None, None) is True
        assert _call("=", 1, 1.0) is True  # plain ==, no type strictness


class TestNot:
    def test_negation(self):
        assert _call("not", True) is False
        assert _call("not", None) is True
        assert _call("not", 0) is True
        assert _call("not", [1]) is False


class TestContains:
    def test_string_membership(self):
        assert _call("contains?", "hello world", "world") is True
        assert _call("contains?", "hello", "z") is False

    def test_string_collection_requires_string_item(self):
        # A non-string item against a string coll is False, not an error.
        assert _call("contains?", "hello", 1) is False

    def test_vector_membership(self):
        assert _call("contains?", [1, 2, 3], 2) is True
        assert _call("contains?", [1, 2, 3], 9) is False
        assert _call("contains?", (1, 2), 2) is True  # tuples count as vectors

    def test_other_collection_types_rejected(self):
        with pytest.raises(EvalError, match=r"contains\? expects a vector or string, got int"):
            _call("contains?", 42, 1)
        with pytest.raises(EvalError, match=r"contains\? expects a vector or string, got dict"):
            _call("contains?", {"a": 1}, "a")


class TestGet:
    """``get`` is total: it never raises, whatever the payload."""

    def test_present_key(self):
        assert _call("get", {"a": 1}, "a") == 1

    def test_missing_key_yields_default(self):
        assert _call("get", {"a": 1}, "b") is None
        assert _call("get", {"a": 1}, "b", 0) == 0

    def test_non_dict_target_yields_default(self):
        for target in (None, 42, "str", [1, 2], Keyword("k")):
            assert _call("get", target, "a") is None
            assert _call("get", target, "a", "fallback") == "fallback"


class TestStr:
    def test_strings_concatenate_verbatim(self):
        assert _call("str", "a", "b", "c") == "abc"

    def test_non_strings_render_in_edn_write_form(self):
        assert _call("str", Keyword("foo")) == ":foo"
        assert _call("str", [1, 2]) == "[1 2]"
        assert _call("str", None) == "nil"
        assert _call("str", True) == "true"
        assert _call("str", "n=", 3) == "n=3"

    def test_no_args_is_empty_string(self):
        assert _call("str") == ""


class TestStringVocabulary:
    """``split`` / ``join`` / ``substring`` — pure, strict on types."""

    def test_split(self):
        assert _call("split", "a,b,c", ",") == ["a", "b", "c"]
        assert _call("split", "no-sep", ",") == ["no-sep"]
        assert _call("split", "", ",") == [""]

    def test_split_rejects_empty_separator_and_non_strings(self):
        with pytest.raises(EvalError, match="separator must be non-empty"):
            _call("split", "abc", "")
        with pytest.raises(EvalError, match="split expects a string"):
            _call("split", Keyword("a,b"), ",")
        with pytest.raises(EvalError, match="split expects a string"):
            _call("split", "a,b", None)

    def test_join(self):
        assert _call("join", ", ", ["a", "b"]) == "a, b"
        assert _call("join", "-", []) == ""

    def test_join_rejects_non_vector_and_non_string_items(self):
        with pytest.raises(EvalError, match="join expects a vector"):
            _call("join", ",", "abc")
        with pytest.raises(EvalError, match="join expects a string"):
            _call("join", ",", ["a", 1])

    def test_substring(self):
        assert _call("substring", "hello world", 6) == "world"
        assert _call("substring", "hello", 0, 2) == "he"
        assert _call("substring", "hello", -2) == "lo"  # negative from the end
        assert _call("substring", "hi", 0, 99) == "hi"  # out-of-range clamps

    def test_substring_rejects_non_int_bounds(self):
        with pytest.raises(EvalError, match="substring start must be an int"):
            _call("substring", "hello", True)
        with pytest.raises(EvalError, match="substring end must be an int"):
            _call("substring", "hello", 0, 1.5)


class TestArithmetic:
    def test_add(self):
        assert _call("+") == 0  # additive identity
        assert _call("+", 1, 2, 3) == 6
        assert _call("+", 1.5, 2) == 3.5

    def test_multiply(self):
        assert _call("*") == 1  # multiplicative identity
        assert _call("*", 2, 3, 4) == 24

    def test_subtract(self):
        assert _call("-", 5) == -5  # unary negation
        assert _call("-", 5, 2) == 3
        assert _call("-", -3) == 3

    def test_divide(self):
        assert _call("/", 7, 2) == 3.5  # always float division
        assert _call("/", 4, 2) == 2.0
        assert _call("/", 24, 2, 3) == 4.0  # left fold: 24 / 2 / 3
        assert _call("/", 1, 4) == 0.25

    def test_divide_by_zero_rejected(self):
        with pytest.raises(EvalError, match="division by zero"):
            _call("/", 1, 0)
        with pytest.raises(EvalError, match="division by zero"):
            _call("/", 1, 2, 0)

    def test_inc_dec(self):
        assert _call("inc", 4) == 5
        assert _call("dec", 4) == 3
        assert _call("inc", 0.5) == 1.5

    def test_comparisons(self):
        assert _call("<", 1, 2) is True
        assert _call("<", 2, 1) is False
        assert _call(">", 2, 1) is True
        assert _call(">", 1, 2) is False
        assert _call("<=", 1, 1) is True
        assert _call("<=", 2, 1) is False
        assert _call(">=", 1, 1) is True
        assert _call(">=", 1, 2) is False


class TestNumCoercion:
    """``_num`` rejects bool (an int subclass) and every non-number."""

    @pytest.mark.parametrize("name", ["+", "-", "*", "/", "<", ">", "<=", ">="])
    def test_bool_is_not_a_number(self, name: str):
        with pytest.raises(EvalError, match="expected a number, got bool"):
            _call(name, True, 1)

    @pytest.mark.parametrize("name", ["inc", "dec"])
    def test_bool_is_not_a_number_unary(self, name: str):
        with pytest.raises(EvalError, match="expected a number, got bool"):
            _call(name, True)

    @pytest.mark.parametrize("value", ["1", None, [1], {"a": 1}])
    def test_non_numbers_rejected(self, value: Any):
        with pytest.raises(EvalError, match=f"expected a number, got {type(value).__name__}"):
            _call("+", value)

    def test_keyword_rejected(self):
        with pytest.raises(EvalError, match="expected a number, got Keyword"):
            _call("+", Keyword("a"))
