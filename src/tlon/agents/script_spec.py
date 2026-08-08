"""ScriptSpec — policy as data for script members (phase P5a).

Per the member design (docs/design/members.md, 阶段 D), a script member's
policy is data, not code. ``ScriptSpec`` is a typed, pure-data dataclass
tree; EDN is only a serialization frontend that parses into it (the same
frontend role ``behavior.tlon`` plays for ``AgentSpec``). Schema v1::

    {:version 1
     :rules [{:name "wake"                   ; required, unique
              :on :mail-delivered           ; required: event keyword, or :boot
              :when (sleeping? (get event :recipient))  ; optional pure predicate
              :actions [(start {:agent (get event :recipient) :reason "wake"})]}
             ...]}

- ``:on`` keywords map to EventType names (kebab-case to upper-snake:
  ``:mail-delivered`` -> MAIL_DELIVERED); ``:boot`` marks a rule that runs
  once after subscriptions are in place. Unknown events are validation
  errors.
- ``:when`` defaults to always-true; the expression is statically
  validated at load time against the pure subset of the evaluation
  registry (scope: ``event`` / ``self``).
- ``:actions`` is a required non-empty vector of action forms, statically
  validated at load time with the two purity positions (same rule as
  on-complete's ``validate_action_form``): the rule's ``:when`` is the
  pure position; each action position must be an action form
  (:data:`~tlon.agents.eval.SCRIPT_ACTION_NAMES`) whose arguments are
  pure expressions.

The spec is **total** (same style as ``AgentSpec``): missing sections or
type errors raise :class:`ScriptSpecValidationError` — there are no
defaults. Unknown keys are ignored with a warning for forward
compatibility.

Template parameters (parameterized prototypes): an optional
``:parameters`` section declares variables that the spec accepts at
instantiation time. Each variable is a ``${name}`` placeholder in the
source text. The text is not valid EDN before substitution — ``loads()``
replaces ``${name}`` with type-safe placeholders for validation, then
``render(params)`` substitutes the real values when an instance is
created. Syntax: ``${var}``, with ``$$`` as the literal ``$`` escape.

    {:version 1
     :parameters {:to {:type :string-list :required true}}
     :rules [{:name "relay"
              :on :mail-delivered
              :actions [(forward {:to ${to}})]}]}
"""

import re
from dataclasses import dataclass, field
from typing import Any

from tlon.agents.edn import EDNParseError, Form, Keyword, Symbol, dumps, loads
from tlon.agents.eval import (
    SCRIPT_ACTION_NAMES,
    Builtin,
    EvalError,
    make_script_builtins,
    validate_form,
)
from tlon.base.events.event import EventType
from tlon.base.logger import get_logger
from tlon.base.paths import paths

logger = get_logger(__name__)

SCRIPT_SPEC_VERSION = 1
SCRIPT_FILE_NAME = "script.tlon"

_RULE_KEYS = frozenset({"name", "on", "when", "actions"})
_PARAM_KEYS = frozenset({"type", "required"})
_PARAM_TYPES = frozenset({"string", "string-list"})
_TEMPLATE_RE = re.compile(r'\$\{(\w+)\}')


class ScriptSpecValidationError(ValueError):
    """A script spec failed EDN-level semantic validation."""


@dataclass(frozen=True)
class ParameterSpec:
    """One template parameter declaration.

    ``name`` is the variable name (referenced as ``${name}`` in the spec
    text). ``type`` is ``"string"`` or ``"string-list"`` — the EDN type
    the parameter value will be serialized to. ``required`` (default True)
    controls whether a value must be passed at instantiation.
    """

    name: str
    type: str  # "string" | "string-list"
    required: bool = True


@dataclass(frozen=True)
class ScriptRule:
    """One policy rule: on an event, gated by a pure predicate, act.

    ``on`` is the triggering event type; ``None`` marks a ``:boot`` rule
    that runs once after subscriptions are in place. ``when`` is an
    optional pure predicate expression over ``event`` / ``self`` (None =
    always true), statically validated at load time. ``actions`` holds
    parsed action forms, statically validated at load time; evaluation
    returns ActionRequest data the engine executes.
    """

    name: str
    on: EventType | None
    when: Form | None
    actions: tuple[Form, ...]


@dataclass(frozen=True)
class ScriptSpec:
    """Complete policy spec for one script member (semantics authority)."""

    version: int
    rules: tuple[ScriptRule, ...]
    parameters: tuple[ParameterSpec, ...] = ()
    _template_text: str | None = field(default=None, init=False, compare=False, repr=False)

    def has_parameters(self) -> bool:
        """Whether this spec was loaded from a parameterized template."""
        return self._template_text is not None

    def render(self, params: dict[str, Any]) -> str:
        """Substitute template parameters, returning valid EDN text.

        Every declared parameter must be provided; extra keys are
        rejected. Values are serialized to EDN via :func:`dumps` and
        replaced into the template text.

        Args:
            params: ``{name: value}`` — string values for ``:string``
                parameters, list-of-string for ``:string-list``.

        Returns:
            Valid EDN text ready to write as ``script.tlon``.

        Raises:
            ScriptSpecValidationError: missing required, unknown key,
                wrong type, or spec was not loaded from a template.
        """
        if self._template_text is None:
            raise ScriptSpecValidationError(
                "Cannot render: spec was not loaded from a parameterized template"
            )
        param_map = {p.name: p for p in self.parameters}
        required = {p.name for p in self.parameters if p.required}

        given = set(params.keys())
        missing = required - given
        if missing:
            raise ScriptSpecValidationError(
                f"Missing required parameters: {sorted(missing)}"
            )
        extra = given - set(param_map)
        if extra:
            raise ScriptSpecValidationError(
                f"Unknown parameters: {sorted(extra)}"
            )

        # Type-check and collect replacements
        replacements: dict[str, str] = {}
        for name, value in params.items():
            p = param_map[name]
            if p.type == "string":
                if not isinstance(value, str):
                    raise ScriptSpecValidationError(
                        f"Parameter {name!r}: expected string, "
                        f"got {_type_name(value)}"
                    )
                replacements[name] = dumps(value)
            elif p.type == "string-list":
                if not isinstance(value, (list, tuple)) or not all(
                    isinstance(v, str) for v in value
                ):
                    raise ScriptSpecValidationError(
                        f"Parameter {name!r}: expected list of strings"
                    )
                replacements[name] = dumps(list(value))
            else:
                raise ScriptSpecValidationError(
                    f"Unknown parameter type {p.type!r}"
                )

        # Substitute: replace ${name} with the serialized value
        def _replacer(m: re.Match[str]) -> str:
            name = m.group(1)
            return replacements.get(name, m.group(0))

        return _TEMPLATE_RE.sub(_replacer, self._template_text)

    @classmethod
    def loads(
        cls, text: str, builtins: dict[str, Builtin] | None = None
    ) -> "ScriptSpec":
        """Parse and validate script spec EDN text, with template support.

        When the text contains ``:parameters``, ``${var}`` placeholders
        are replaced with type-safe stand-in values before EDN parsing
        and validation. The original text is preserved so
        :meth:`render` can substitute real values later.

        When there are no ``:parameters`` the text is parsed directly
        (backward compatible with non-parameterized specs).

        Args:
            text: Raw EDN text (may contain ``${var}`` placeholders).
            builtins: Evaluation registry (defaults to host-less).

        Returns:
            A spec ready for use; call :meth:`render` on it when
            concrete parameter values are available.

        Raises:
            ScriptSpecValidationError: schema or template validation failure.
            EDNParseError: text is not valid EDN (after placeholder
                substitution).
        """
        if builtins is None:
            builtins = make_script_builtins(None)

        # Quick check: no placeholders → direct parse (fast path)
        placeholders = set(_TEMPLATE_RE.findall(text))
        if not placeholders:
            return cls.from_edn(loads(text), builtins)

        # ---- Template path ----
        # Phase 1: replace every ${var} with nil, parse to extract :parameters
        phase1_text = _TEMPLATE_RE.sub("nil", text)
        try:
            phase1_data = loads(phase1_text)
        except EDNParseError as e:
            raise ScriptSpecValidationError(
                f"template spec parse failed (with placeholder substitution): {e}"
            ) from e

        if not isinstance(phase1_data, dict):
            raise ScriptSpecValidationError(
                f"script spec must be a top-level map, got {_type_name(phase1_data)}"
            )
        params_key = _find_key(phase1_data, "parameters")
        if params_key is None:
            raise ScriptSpecValidationError(
                f"template placeholders {sorted(placeholders)} found "
                f"but no :parameters section declared"
            )
        raw_params = phase1_data[params_key]
        param_specs = _parse_parameters(raw_params, placeholders)

        # Phase 2: replace ${var} with type-appropriate placeholders for
        # full validation (the "shape" must validate even without real values)
        def _standin(name: str) -> str:
            for p in param_specs:
                if p.name == name:
                    return '[""]' if p.type == "string-list" else '""'
            return "nil"  # unreachable: _parse_parameters validates this

        phase2_text = text
        for name in placeholders:

            def _phase2_replacer(m: re.Match[str], *, _name: str = name) -> str:
                return _standin(_name) if m.group(1) == _name else m.group(0)

            phase2_text = _TEMPLATE_RE.sub(_phase2_replacer, phase2_text)

        try:
            spec = cls.from_edn(loads(phase2_text), builtins)
        except (EDNParseError, ScriptSpecValidationError) as e:
            raise ScriptSpecValidationError(
                f"template spec validation failed: {e}"
            ) from e

        object.__setattr__(spec, "_template_text", text)
        object.__setattr__(spec, "parameters", param_specs)
        return spec

    @classmethod
    def from_edn(
        cls, data: Any, builtins: dict[str, Builtin] | None = None
    ) -> "ScriptSpec":
        """Build a spec from a parsed EDN map — total validation.

        ``builtins`` is the evaluation registry the spec will run
        against, host predicate closures included (built by the caller
        via :func:`~tlon.agents.eval.make_script_builtins`); ``:when``
        expressions validate against its pure subset, action forms
        against the script action names. Defaults to a host-less
        registry.

        Raises:
            ScriptSpecValidationError: missing sections, type errors,
                unknown events, or invalid expressions
        """
        if builtins is None:
            builtins = make_script_builtins(None)
        if not isinstance(data, dict):
            raise ScriptSpecValidationError(
                f"script spec must be a top-level map, got {_type_name(data)}"
            )
        fields = {str(key): value for key, value in data.items()}
        if "version" not in fields:
            raise ScriptSpecValidationError("missing required :version")
        version = fields["version"]
        if not isinstance(version, int) or isinstance(version, bool) or version != SCRIPT_SPEC_VERSION:
            raise ScriptSpecValidationError(
                f"unsupported :version {version!r}; "
                f"only version {SCRIPT_SPEC_VERSION} is supported"
            )
        if "rules" not in fields:
            raise ScriptSpecValidationError(
                "missing required :rules "
                "(a script spec is total: no partial specs, no defaults)"
            )
        return cls(version=SCRIPT_SPEC_VERSION, rules=_rules_from(fields["rules"], builtins))

    @classmethod
    def load(cls, agent_id: str) -> "ScriptSpec":
        """Load a script member's spec from its script file — total semantics.

        ``~/.tlon/{agent_id}/config/script.tlon`` must exist, parse as
        EDN, and validate as a complete spec. Any failure raises: the
        member refuses to boot (the registry logs and isolates the
        failure; the server and other agents are unaffected). Mirrors
        :meth:`tlon.agents.spec.AgentSpec.load`.

        Validation runs against the host-less registry: script-instance
        specs may use the pure accessors and the action forms, not the
        host predicates (``sleeping?`` etc.) — those are Adam's vocabulary.

        Args:
            agent_id: id of the member whose script file to load

        Raises:
            ScriptSpecValidationError: file missing, unreadable, or failed
                schema validation
            EDNParseError: file is not valid EDN
        """
        script_file = paths.agent_script_file(agent_id)
        if not script_file.exists():
            logger.error(
                "script_spec_invalid",
                agent_id=agent_id,
                path=str(script_file),
                reason="script file missing",
            )
            raise ScriptSpecValidationError(
                f"script file missing, agent refused to boot: {script_file}"
            )
        try:
            raw = script_file.read_text(encoding="utf-8")
            spec = cls.loads(raw)
        except (EDNParseError, ScriptSpecValidationError, OSError) as e:
            logger.error(
                "script_spec_invalid",
                agent_id=agent_id,
                path=str(script_file),
                reason=str(e),
            )
            raise
        logger.info("script_spec_loaded", agent_id=agent_id, path=str(script_file))
        return spec


def _rules_from(value: Any, builtins: dict[str, Builtin]) -> tuple[ScriptRule, ...]:
    """Read the ``:rules`` vector: each item must be a rule map, names unique."""
    if isinstance(value, Form) or not isinstance(value, (list, tuple)):
        raise ScriptSpecValidationError(
            f":rules must be a vector, got {_type_name(value)}: {value!r}"
        )
    if not value:
        raise ScriptSpecValidationError(":rules must not be empty")
    rules: list[ScriptRule] = []
    seen: set[str] = set()
    for item in value:
        rule = _rule_from(item, builtins)
        if rule.name in seen:
            raise ScriptSpecValidationError(f"duplicate rule name {rule.name!r} in :rules")
        seen.add(rule.name)
        rules.append(rule)
    return tuple(rules)


def _rule_from(item: Any, builtins: dict[str, Builtin]) -> ScriptRule:
    """Build one rule from its map — every required field must be present."""
    if not isinstance(item, dict):
        raise ScriptSpecValidationError(
            f":rules items must be maps, got {_type_name(item)}: {item!r}"
        )
    fields = {str(key): value for key, value in item.items()}
    for key in fields:
        if key not in _RULE_KEYS:
            logger.warning("script_spec_unknown_key", section="rule", key=key)
    name = fields.get("name")
    if type(name) is not str or not name:
        raise ScriptSpecValidationError(
            f"rule :name must be a non-empty string, got {name!r}"
        )
    if "on" not in fields:
        raise ScriptSpecValidationError(f"rule {name!r}: missing required :on")
    if "actions" not in fields:
        raise ScriptSpecValidationError(f"rule {name!r}: missing required :actions")
    return ScriptRule(
        name=name,
        on=_on_from(fields["on"], name),
        when=_when_from(fields.get("when"), name, builtins),
        actions=_actions_from(fields["actions"], name, builtins),
    )


def _on_from(value: Any, rule_name: str) -> EventType | None:
    """Map an ``:on`` keyword to an EventType; ``:boot`` becomes None."""
    if not isinstance(value, Keyword):
        raise ScriptSpecValidationError(
            f"rule {rule_name!r}: :on must be an event keyword, "
            f"got {_type_name(value)}: {value!r}"
        )
    if str(value) == "boot":
        return None
    event_name = str(value).replace("-", "_").upper()
    try:
        return EventType[event_name]
    except KeyError:
        raise ScriptSpecValidationError(
            f"rule {rule_name!r}: unknown event :{value} in :on"
        ) from None


def _when_from(value: Any, rule_name: str, builtins: dict[str, Builtin]) -> Form | None:
    """Read the optional ``:when`` gate; statically validated, never executed."""
    if value is None:
        return None
    if not isinstance(value, Form):
        raise ScriptSpecValidationError(
            f"rule {rule_name!r}: :when must be an expression, "
            f"got {_type_name(value)}: {value!r}"
        )
    pure = {name: builtin for name, builtin in builtins.items() if builtin.pure}
    try:
        validate_form(value, pure, scope={"event", "self"})
    except EvalError as e:
        raise ScriptSpecValidationError(
            f"rule {rule_name!r}: invalid :when expression: {e}"
        ) from e
    return value


def _actions_from(
    value: Any, rule_name: str, builtins: dict[str, Builtin]
) -> tuple[Form, ...]:
    """Read the ``:actions`` vector: non-empty, every item an action form."""
    if isinstance(value, Form) or not isinstance(value, (list, tuple)):
        raise ScriptSpecValidationError(
            f"rule {rule_name!r}: :actions must be a vector, "
            f"got {_type_name(value)}: {value!r}"
        )
    if not value:
        raise ScriptSpecValidationError(f"rule {rule_name!r}: :actions must not be empty")
    forms: list[Form] = []
    for item in value:
        if not isinstance(item, Form):
            raise ScriptSpecValidationError(
                f"rule {rule_name!r}: :actions items must be action forms, "
                f"got {_type_name(item)}: {item!r}"
            )
        _validate_action(item, rule_name, builtins)
        forms.append(item)
    return tuple(forms)


def _validate_action(form: Form, rule_name: str, builtins: dict[str, Builtin]) -> None:
    """Statically validate one action form (two purity positions, no execution).

    The action position must be a whitelisted action form; its arguments
    are the pure position — only pure builtins and the ``event`` /
    ``self`` symbols are accepted there.
    """
    if not form:
        raise ScriptSpecValidationError(
            f"rule {rule_name!r}: empty form () is not an action"
        )
    head = form[0]
    if not isinstance(head, Symbol) or str(head) not in SCRIPT_ACTION_NAMES:
        raise ScriptSpecValidationError(
            f"rule {rule_name!r}: unknown action {dumps(head)} "
            f"(allowed: {sorted(SCRIPT_ACTION_NAMES)})"
        )
    pure = {name: builtin for name, builtin in builtins.items() if builtin.pure}
    for arg in form[1:]:
        try:
            validate_form(arg, pure, scope={"event", "self"})
        except EvalError as e:
            raise ScriptSpecValidationError(
                f"rule {rule_name!r}: invalid action argument: {e}"
            ) from e


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


def _find_key(data: dict[Any, Any], target: str) -> Any:
    """Find a key in the map matching ``target`` as string or Keyword."""
    for key in data:
        if str(key) == target:
            return key
    return None


_VALID_PARAM_TYPES = frozenset({"string", "string-list"})


def _parse_parameters(
    value: Any, placeholders: set[str]
) -> tuple[ParameterSpec, ...]:
    """Parse and validate the ``:parameters`` map from a template spec.

    Every ``:parameters`` key must name a placeholder found in the text;
    every placeholder in the text must be declared in ``:parameters``.
    """
    if not isinstance(value, dict):
        raise ScriptSpecValidationError(
            f":parameters must be a map, got {_type_name(value)}"
        )
    if not value:
        raise ScriptSpecValidationError(":parameters must not be empty")

    specs: list[ParameterSpec] = []
    declared: set[str] = set()
    for key, pval in value.items():
        if not isinstance(key, (Keyword, str)):
            raise ScriptSpecValidationError(
                f":parameters key must be a keyword or string, "
                f"got {_type_name(key)}: {key!r}"
            )
        name = str(key)
        if name in declared:
            raise ScriptSpecValidationError(
                f"duplicate parameter name {name!r} in :parameters"
            )
        declared.add(name)
        if not isinstance(pval, dict):
            raise ScriptSpecValidationError(
                f"parameter {name!r}: value must be a map, "
                f"got {_type_name(pval)}"
            )
        pfields = {str(k): v for k, v in pval.items()}
        for pk in pfields:
            if pk not in _PARAM_KEYS:
                logger.warning(
                    "script_spec_unknown_key",
                    section="parameters",
                    key=pk,
                )
        ptype_str = str(pfields.get("type") or "string")
        if ptype_str not in _VALID_PARAM_TYPES:
            raise ScriptSpecValidationError(
                f"parameter {name!r}: :type must be one of "
                f"{sorted(_VALID_PARAM_TYPES)}, got {ptype_str!r}"
            )
        required = bool(pfields.get("required", True))
        specs.append(ParameterSpec(name=name, type=ptype_str, required=required))

    if not placeholders.issubset(declared):
        undeclared = placeholders - declared
        raise ScriptSpecValidationError(
            f"template placeholders not declared in :parameters: "
            f"{sorted(undeclared)}"
        )
    if not declared.issubset(placeholders):
        unused = declared - placeholders
        raise ScriptSpecValidationError(
            f":parameters declares variables not used in template: "
            f"{sorted(unused)}"
        )

    return tuple(specs)
