"""Admission caches only successful meta-validation, never mutable contracts."""

from collections.abc import Iterator
from typing import Any
from unittest.mock import Mock

import pytest
from jsonschema import Draft202012Validator

from tlon.operations.schema import (
    DefinitionError,
    InputError,
    OutputError,
    _check_schema_cached,
    admit_schema,
    validate,
)


@pytest.fixture(autouse=True)
def isolated_cache() -> Iterator[None]:
    _check_schema_cached.cache_clear()
    yield
    _check_schema_cached.cache_clear()


@pytest.fixture
def meta_check(monkeypatch: pytest.MonkeyPatch) -> Mock:
    check = Mock(wraps=Draft202012Validator.check_schema)
    monkeypatch.setattr(Draft202012Validator, "check_schema", check)
    return check


def test_content_key_ignores_object_insertion_order(meta_check: Mock) -> None:
    admit_schema({"type": "string", "minLength": 1})
    admit_schema({"minLength": 1, "type": "string"})
    assert meta_check.call_count == 1


def test_schema_mutations_do_not_change_other_admissions(meta_check: Mock) -> None:
    source: dict[str, Any] = {"type": "array", "items": {"type": "string"}}
    first = admit_schema(source)
    assert isinstance(first, dict)
    first["items"]["type"] = "number"
    assert admit_schema(source) == {"type": "array", "items": {"type": "string"}}
    source["items"]["type"] = "integer"
    assert admit_schema(source) == source
    assert meta_check.call_count == 2
    source["items"]["unexpected"] = True
    with pytest.raises(DefinitionError, match="unsupported schema keywords"):
        admit_schema(source)


def test_boolean_integer_float_keys_do_not_collide(meta_check: Mock) -> None:
    admit_schema({"type": "number", "minimum": 1})
    admit_schema({"type": "number", "minimum": 1.0})
    with pytest.raises(DefinitionError):
        admit_schema({"type": "number", "minimum": True})
    assert meta_check.call_count == 3
    assert _check_schema_cached.cache_info().currsize == 2


def test_strict_json_boundary_is_not_bypassed_by_warm_cache(meta_check: Mock) -> None:
    class Text(str):
        pass

    class Object(dict[str, Any]):
        pass

    class Sequence(list[str]):
        pass

    admit_schema({"type": "string", "enum": ["x"]})
    for source in (
        Object(type="string", enum=["x"]),
        {"type": Text("string"), "enum": ["x"]},
        {"type": "string", "enum": Sequence(["x"])},
        {"type": "string", "enum": ("x",)},
        {"type": "string", "enum": [{1: "x"}]},
    ):
        with pytest.raises(DefinitionError):
            admit_schema(source)  # type: ignore[arg-type]
    assert meta_check.call_count == 1


def test_output_admission_does_not_bypass_input_closed_object_check(meta_check: Mock) -> None:
    for schema in (
        True,
        {"type": "object", "properties": {}, "required": [], "additionalProperties": True},
    ):
        admit_schema(schema)
        with pytest.raises(DefinitionError, match="input schema must be a closed object"):
            admit_schema(schema, input_schema=True)
    assert meta_check.call_count == 2


def test_failed_meta_validation_is_not_cached(meta_check: Mock) -> None:
    for _ in range(2):
        with pytest.raises(DefinitionError):
            admit_schema({"type": "string", "minLength": -1})
    assert meta_check.call_count == 2
    assert _check_schema_cached.cache_info().currsize == 0


def test_cache_is_bounded_and_evicted_schema_is_rechecked(meta_check: Mock) -> None:
    for length in range(257):
        admit_schema({"type": "string", "minLength": length})
    assert _check_schema_cached.cache_info().currsize == 256
    admit_schema({"type": "string", "minLength": 0})
    assert meta_check.call_count == 258


def test_large_schema_bypasses_cache(meta_check: Mock) -> None:
    schema = {"type": "string", "description": "x" * 65_537}
    assert admit_schema(schema) == schema
    assert admit_schema(schema) == schema
    assert meta_check.call_count == 2
    assert _check_schema_cached.cache_info().currsize == 0


def test_admission_cache_does_not_cache_input_or_output_validation() -> None:
    schema = {"type": "integer", "minimum": 1}
    admit_schema(schema)
    admit_schema(schema)
    assert validate(schema, 1) == 1
    with pytest.raises(InputError):
        validate(schema, 0)
    with pytest.raises(OutputError):
        validate(schema, 0, output=True)
