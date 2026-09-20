"""Write-side behavior template layer (phase C).

A ``behavior.tlon`` may declare a top-level ``:parameters`` map
(``{:to {:type :string-list :required true}}``) and reference the
variables as ``${to}`` inside rule forms. This is a **write-side only**
layer: the runtime loader ignores ``:parameters`` (it warns on the
unknown key) and never sees ``${var}`` placeholders, because
instantiation renders them away before the file is written.

Relocated from the legacy ``agents/script_spec.py`` template machinery:
the unified file format keeps the same template convention.
"""

import re
from dataclasses import dataclass
from typing import Any

from tlosure.edn import EDNParseError, Keyword, dumps, loads

_TEMPLATE_RE = re.compile(r"\$\{(\w+)\}")
_PARAM_KEYS = frozenset({"type", "required"})
_VALID_PARAM_TYPES = frozenset({"string", "string-list"})


class BehaviorTemplateError(ValueError):
    """A behavior template failed to parse, declare, or render."""


@dataclass(frozen=True)
class ParameterSpec:
    """One template parameter declaration (referenced as ``${name}``)."""

    name: str
    type: str  # "string" | "string-list"
    required: bool = True


@dataclass(frozen=True)
class BehaviorTemplate:
    """Parsed declaration of a behavior template.

    ``placeholders`` is empty for a concrete (non-template) behavior;
    ``standin_text`` is the text with every ``${var}`` replaced by a
    type-appropriate stand-in (``""`` / ``[""]``) — concrete enough for
    structural validation, without ever executing anything.
    """

    parameters: tuple[ParameterSpec, ...]
    placeholders: frozenset[str]
    standin_text: str

    @property
    def has_parameters(self) -> bool:
        """Whether the behavior declares template parameters."""
        return bool(self.placeholders)


def analyze(text: str) -> BehaviorTemplate:
    """Parse and validate a behavior template's ``:parameters`` declaration.

    No placeholders → an empty template (the text is concrete). Otherwise
    the text must parse as EDN (placeholders substituted to nil) and
    declare every ``${var}`` in ``:parameters`` with a valid ``:type``.

    Raises:
        BehaviorTemplateError: undeclared placeholder, malformed
            ``:parameters`` map, or unparseable text.
    """
    placeholders = set(_TEMPLATE_RE.findall(text))
    if not placeholders:
        return BehaviorTemplate(parameters=(), placeholders=frozenset(), standin_text=text)

    phase1 = _TEMPLATE_RE.sub("nil", text)
    try:
        data = loads(phase1)
    except EDNParseError as e:
        raise BehaviorTemplateError(f"template parse failed: {e}") from e
    if not isinstance(data, dict):
        raise BehaviorTemplateError("behavior template must be a top-level map")
    key = _find_key(data, "parameters")
    if key is None:
        raise BehaviorTemplateError(
            f"template placeholders {sorted(placeholders)} found but no :parameters declared"
        )
    parameters = _parse_parameters(data[key], placeholders)
    return BehaviorTemplate(
        parameters=parameters,
        placeholders=frozenset(placeholders),
        standin_text=_standin_substitute(text, parameters, placeholders),
    )


def render_template(text: str, params: dict[str, Any]) -> str:
    """Substitute template parameters, returning valid EDN text.

    Every declared required parameter must be provided; extra keys are
    rejected. Values are serialized to EDN (:func:`dumps`) and replaced
    into the template text. Concrete behavior (no placeholders) returns
    ``text`` unchanged (and rejects a non-empty ``params``).

    Raises:
        BehaviorTemplateError: missing required, unknown key, wrong type,
            or params passed to a concrete behavior.
    """
    info = analyze(text)
    if not info.has_parameters:
        if params:
            raise BehaviorTemplateError(
                "behavior does not declare :parameters, but parameters were provided"
            )
        return text

    param_map = {p.name: p for p in info.parameters}
    required = {p.name for p in info.parameters if p.required}
    given = set(params)
    missing = required - given
    if missing:
        raise BehaviorTemplateError(f"missing required parameters: {sorted(missing)}")
    extra = given - set(param_map)
    if extra:
        raise BehaviorTemplateError(f"unknown parameters: {sorted(extra)}")

    replacements: dict[str, str] = {}
    for name, value in params.items():
        p = param_map[name]
        if p.type == "string":
            if not isinstance(value, str):
                raise BehaviorTemplateError(
                    f"parameter {name!r}: expected string, got {_type_name(value)}"
                )
            replacements[name] = dumps(value)
        elif p.type == "string-list":
            if not isinstance(value, (list, tuple)) or not all(
                isinstance(v, str) for v in value
            ):
                raise BehaviorTemplateError(
                    f"parameter {name!r}: expected list of strings"
                )
            replacements[name] = dumps(list(value))
        else:
            raise BehaviorTemplateError(f"unknown parameter type {p.type!r}")

    def _replacer(m: re.Match[str]) -> str:
        return replacements.get(m.group(1), m.group(0))

    return _TEMPLATE_RE.sub(_replacer, text)


def _standin_substitute(
    text: str, parameters: tuple[ParameterSpec, ...], placeholders: set[str]
) -> str:
    """Replace every declared placeholder with a type-appropriate stand-in."""
    standin: dict[str, str] = {}
    for p in parameters:
        if p.name in placeholders:
            standin[p.name] = '[""]' if p.type == "string-list" else '""'

    def _replacer(m: re.Match[str]) -> str:
        return standin.get(m.group(1), m.group(0))

    return _TEMPLATE_RE.sub(_replacer, text)


def _parse_parameters(
    value: Any, placeholders: set[str]
) -> tuple[ParameterSpec, ...]:
    """Parse and validate the ``:parameters`` map from a template.

    Every ``:parameters`` key must name a placeholder found in the text;
    every placeholder in the text must be declared in ``:parameters``.
    """
    if not isinstance(value, dict):
        raise BehaviorTemplateError(
            f":parameters must be a map, got {_type_name(value)}"
        )
    if not value:
        raise BehaviorTemplateError(":parameters must not be empty")

    specs: list[ParameterSpec] = []
    declared: set[str] = set()
    for key, pval in value.items():
        if not isinstance(key, (Keyword, str)):
            raise BehaviorTemplateError(
                f":parameters key must be a keyword or string, "
                f"got {_type_name(key)}: {key!r}"
            )
        name = str(key)
        if name in declared:
            raise BehaviorTemplateError(
                f"duplicate parameter name {name!r} in :parameters"
            )
        declared.add(name)
        if not isinstance(pval, dict):
            raise BehaviorTemplateError(
                f"parameter {name!r}: value must be a map, got {_type_name(pval)}"
            )
        pfields = {str(k): v for k, v in pval.items()}
        for pk in pfields:
            if pk not in _PARAM_KEYS:
                raise BehaviorTemplateError(
                    f"parameter {name!r}: unknown key :{pk}"
                )
        ptype_str = str(pfields.get("type") or "string")
        if ptype_str not in _VALID_PARAM_TYPES:
            raise BehaviorTemplateError(
                f"parameter {name!r}: :type must be one of "
                f"{sorted(_VALID_PARAM_TYPES)}, got {ptype_str!r}"
            )
        required = bool(pfields.get("required", True))
        specs.append(ParameterSpec(name=name, type=ptype_str, required=required))

    if not placeholders.issubset(declared):
        undeclared = placeholders - declared
        raise BehaviorTemplateError(
            f"template placeholders not declared in :parameters: "
            f"{sorted(undeclared)}"
        )
    return tuple(specs)


def _find_key(data: dict[Any, Any], target: str) -> Any:
    """Find a key in the map matching ``target`` as string or Keyword."""
    for key in data:
        if str(key) == target:
            return key
    return None


def _type_name(value: Any) -> str:
    if value is None:
        return "nil"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, Keyword):
        return "keyword"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    return type(value).__name__
