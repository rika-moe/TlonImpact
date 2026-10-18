"""Collection operations return updated data without mutating their inputs."""

from typing import Any

import pytest

from tlosure.builtins import CORE_BUILTINS, EvalError
from tlosure.edn import Form, Symbol


def call(name: str, *args: Any) -> Any:
    fn = CORE_BUILTINS[name].fn
    assert fn is not None
    return fn({}, *args)


def test_map_updates_preserve_input_and_key_order() -> None:
    original = {"alice": 1, "bob": 2}
    updated = call("assoc", original, "alice", 3, "carol", 4)
    removed = call("dissoc", updated, "bob", "missing")
    assert original == {"alice": 1, "bob": 2}
    assert updated == {"alice": 3, "bob": 2, "carol": 4}
    assert removed == {"alice": 3, "carol": 4}
    assert call("keys", removed) == ["alice", "carol"]
    assert call("vals", removed) == [3, 4]


def test_vector_assoc_supports_replace_and_append() -> None:
    original = [1, 2]
    assert call("assoc", original, 1, 20, 2, 30) == [1, 20, 30]
    assert original == [1, 2]
    assert call("assoc", original) == original
    assert call("assoc", original) is not original


def test_conj_accepts_vectors_or_map_entries() -> None:
    original = [1]
    assert call("conj", original, 2, 3) == [1, 2, 3]
    assert original == [1]
    mapping = {"a": 1}
    assert call("conj", mapping, ["a", 2], ["b", 3]) == {"a": 2, "b": 3}
    assert mapping == {"a": 1}


@pytest.mark.parametrize("index", [-1, 3, True, "0"])
def test_assoc_rejects_invalid_vector_indices(index: Any) -> None:
    with pytest.raises(EvalError, match="index"):
        call("assoc", [1], index, 2)


@pytest.mark.parametrize(
    "name,args",
    [
        ("assoc", ({}, "a")),
        ("assoc", ({}, [], 1)),
        ("assoc", (None, "a", 1)),
        ("assoc", (Form([Symbol("x")]), 0, 1)),
        ("conj", ({}, ["a"])),
        ("conj", ({}, [[1], 2])),
        ("conj", ("text", 1)),
        ("dissoc", ({}, [])),
        ("dissoc", ([1], 0)),
        ("keys", ([1],)),
        ("vals", ([1],)),
    ],
)
def test_collection_errors_are_explicit(name: str, args: tuple[Any, ...]) -> None:
    with pytest.raises(EvalError):
        call(name, *args)
