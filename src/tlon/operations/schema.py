"""Strict JSON data boundary and admitted JSON Schema 2020-12 subset."""

import math
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from tlosure.edn import Keyword
from tlosure.eval import EvalError


class OperationError(EvalError):
    """A failure at the shared operation boundary."""


class RejectedError(OperationError):
    """Known rejection of a business operation."""

    def __init__(self, message: str, data: dict[str, Any]) -> None:
        super().__init__(message)
        self.data = data


class UnknownOutcomeError(OperationError):
    """The operation may have committed; retry with its stable request id."""

    def __init__(self, message: str, data: dict[str, Any]) -> None:
        super().__init__(message)
        self.data = data


class InputError(OperationError):
    """Arguments do not satisfy the operation contract."""


class OutputError(OperationError):
    """Handler output does not satisfy the operation contract; do not retry blindly."""


class DefinitionError(ValueError):
    """An operation definition cannot be admitted."""


_KEYWORDS = frozenset(
    {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "enum",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "uniqueItems",
        "description",
        "title",
        "$schema",
    }
)
_DIALECT = "https://json-schema.org/draft/2020-12/schema"


def json_value(value: Any, *, edn_keys: bool = False) -> Any:
    """Copy strict JSON data; reject code, ambiguous keys, cycles and depth >100.

    DSL accepts Keyword map keys, never Keyword values. Existing Python dicts
    may already have lost equal cross-type keys; the EDN parser rejects those
    before insertion. Exact types exclude EDN str/list subclasses.
    """
    active: set[int] = set()

    def copy(item: Any, depth: int) -> Any:
        if depth > 100:
            raise InputError("JSON nesting exceeds 100")
        if item is None or type(item) in (str, bool, int):
            return item
        if type(item) is float and math.isfinite(item):
            return item
        if type(item) not in (list, dict):
            raise InputError(f"not a JSON value: {type(item).__name__}")
        identity = id(item)
        if identity in active:
            raise InputError("cyclic JSON value")
        active.add(identity)
        try:
            if type(item) is list:
                return [copy(child, depth + 1) for child in item]
            result: dict[str, Any] = {}
            for key, child in item.items():
                if edn_keys and type(key) is Keyword:
                    key = str(key)
                if type(key) is not str:
                    raise InputError("object keys must be strings (DSL also accepts Keyword keys)")
                if key in result:
                    raise InputError(f"duplicate normalized key: {key}")
                result[key] = copy(child, depth + 1)
            return result
        finally:
            active.remove(identity)

    return copy(value, 0)


def edn_value(value: Any) -> Any:
    """Project already validated JSON results to DSL data."""
    if type(value) is dict:
        return {Keyword(key): edn_value(item) for key, item in value.items()}
    if type(value) is list:
        return [edn_value(item) for item in value]
    return value


def admit_schema(schema: dict[str, Any], *, input_schema: bool = False) -> dict[str, Any]:
    """Check schemas once on admission; never silently ignore unknown keywords."""
    try:
        copied: dict[str, Any] = json_value(schema)
    except InputError as error:
        raise DefinitionError(str(error)) from error

    if type(copied) is not dict:
        raise DefinitionError("schema root must be an object")

    def visit(node: Any, path: str) -> None:
        if type(node) is not dict:
            raise DefinitionError(f"{path}: schema must be an object")
        unknown = node.keys() - _KEYWORDS
        if unknown:
            raise DefinitionError(f"{path}: unsupported schema keywords: {sorted(unknown)}")
        if "$schema" in node and node["$schema"] != _DIALECT:
            raise DefinitionError(f"{path}: only JSON Schema 2020-12 is supported")
        declared = node.get("type")
        types = declared if isinstance(declared, list) else [declared]
        if not types or any(
            type(t) is not str
            or t not in {"object", "array", "string", "number", "integer", "boolean", "null"}
            for t in types
        ):
            raise DefinitionError(f"{path}: explicit supported type is required")
        if "object" in types:
            if not {"properties", "required", "additionalProperties"} <= node.keys():
                raise DefinitionError(
                    f"{path}: objects require properties/required/additionalProperties"
                )
            if type(node["additionalProperties"]) is not bool:
                raise DefinitionError(f"{path}: additionalProperties must be boolean")
            if type(node["properties"]) is not dict:
                raise DefinitionError(f"{path}: properties must be an object")
            for name, child in node["properties"].items():
                visit(child, f"{path}.properties.{name}")
        elif {"properties", "required", "additionalProperties"} & node.keys():
            raise DefinitionError(f"{path}: object constraints require object type")
        if "array" in types:
            if "items" not in node:
                raise DefinitionError(f"{path}: arrays require items")
            visit(node["items"], f"{path}.items")
        elif "items" in node:
            raise DefinitionError(f"{path}: items requires array type")

    visit(copied, "schema")
    try:
        Draft202012Validator.check_schema(copied)
    except SchemaError as error:
        raise DefinitionError(error.message) from error
    if input_schema and (copied["type"] != "object" or copied["additionalProperties"] is not False):
        raise DefinitionError("input schema must be a closed object")
    return copied


def validate(schema: dict[str, Any], value: Any, *, output: bool = False) -> Any:
    """Normalize and validate one input/output, returning an isolated copy."""
    error_type = OutputError if output else InputError
    try:
        normalized = json_value(value)
    except InputError as error:
        raise error_type(str(error)) from error
    errors = sorted(
        Draft202012Validator(schema).iter_errors(normalized), key=lambda e: str(list(e.path))
    )
    if errors:
        details = "; ".join(f"{list(error.path)}: {error.message}" for error in errors)
        raise error_type(details)
    return normalized
