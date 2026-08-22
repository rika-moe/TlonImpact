"""MemberSpec — the unified member behavior spec (semantic authority).

Per the unified DSL design (docs/design/dsl-agents.md), the two legacy
specs — AgentSpec (``behavior.tlon``, the ReactAgent cognitive loop) and
ScriptSpec (``script.tlon``, the ScriptMember deterministic rules) —
converge on a single record::

    rule = :on + :when + optional :loop + :actions

This module is the data model of that merge: a typed, pure-data dataclass
tree plus a serialization frontend — EDN is only syntax, the dataclass is
the semantics authority. The legacy specs and their projections
(``agents/spec_mapping.py``) were deleted in phase C: the loader parses
the single rules-form ``behavior.tlon`` directly into MemberSpec, the two
runtimes converge on one interpreter (``agents/member_runtime.py``), and
``runtime`` becomes a derived value (:func:`member_type`). The EDN front
performs structural validation (types, form shapes); the action-head
whitelist and expression-level builtin-scope validation of ``:when`` /
``:until`` / stage and section forms live in the single validator
:func:`validate_member_spec`.
"""

from dataclasses import dataclass
from typing import Any

from tlon.agents.edn import EDNParseError, Form, Keyword, Symbol, dumps, loads
from tlon.agents.eval import (
    LOOP_BUILTINS,
    MAX_FUEL,
    STAGE_COND_BUILTINS,
    Builtin,
    EvalError,
    make_member_builtins,
    validate_form,
    validate_member_action,
)
from tlon.base.events.event import EventType
from tlon.base.logger import get_logger

logger = get_logger(__name__)

MEMBER_SPEC_VERSION = 1

#: Stage names the engine implements (phases 7a/7c). Each stage is a
#: Python-implemented, testable semantic unit; ``act`` means "execute
#: this round's tool calls — all of them", not a single tool call.
#: Relocated from the legacy ``agents/spec.py`` in phase C.
STAGE_NAMES = frozenset({"think", "act", "plan", "critique", "revise"})

#: Section names the engine implements (phase 7b). ``persona`` and
#: ``protocol`` feed the system prompt; ``knowledge`` and
#: ``mail-history`` feed the conversation context, in vector order.
#: Relocated from the legacy ``agents/spec.py`` in phase C.
SECTION_NAMES = frozenset({"persona", "protocol", "knowledge", "mail-history"})


def has_section(sections: tuple[Form, ...], name: str) -> bool:
    """Whether the section vector contains a section named ``name``."""
    return any(str(form[0]) == name for form in sections)


@dataclass(frozen=True)
class ReflectSpec:
    """Periodic background memory audit settings (on a cognitive loop)."""

    enabled: bool
    interval_minutes: int


class MemberSpecValidationError(ValueError):
    """A member spec failed structural (EDN-level) validation."""


@dataclass(frozen=True)
class MemberLoop:
    """The cognitive session a rule runs before its actions.

    ``context_sections`` is the section vector the engine assembles into the
    LLM context; ``stages`` the stage vector and ``until`` the termination
    expression of the engine-executed loop. ``reflect`` is the
    session-completion reflection setting, folded here from the
    legacy ``OnCompleteSpec``. ``fuel`` (phase D) is the self-declared
    budget of the iron-law revision (docs/design/dsl-agents.md): it caps
    the DSL evaluation of the loop's ``:until`` and stage conditions
    (``None`` = the engine default). Bounded above by
    :data:`tlon.agents.eval.MAX_FUEL`.
    """

    context_sections: tuple[Form, ...]
    stages: tuple[Form, ...]
    until: Form
    max_iterations: int
    max_consecutive_errors: int
    reflect: ReflectSpec
    fuel: int | None = None

    # ── Serialization ──

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict (snake_case keys)."""
        return {
            "context": {"sections": [dumps(form) for form in self.context_sections]},
            "stages": [dumps(form) for form in self.stages],
            "until": dumps(self.until),
            "max_iterations": self.max_iterations,
            "max_consecutive_errors": self.max_consecutive_errors,
            "reflect": {
                "enabled": self.reflect.enabled,
                "interval_minutes": self.reflect.interval_minutes,
            },
            "fuel": self.fuel,
        }

    def to_edn_data(self) -> dict[Keyword, Any]:
        """The EDN-native form (kebab-case keywords, Form values)."""
        data: dict[Keyword, Any] = {
            Keyword("context"): {Keyword("sections"): self.context_sections},
            Keyword("stages"): self.stages,
            Keyword("until"): self.until,
            Keyword("max-iterations"): self.max_iterations,
            Keyword("max-consecutive-errors"): self.max_consecutive_errors,
            Keyword("reflect"): {
                Keyword("enabled"): self.reflect.enabled,
                Keyword("interval-minutes"): self.reflect.interval_minutes,
            },
        }
        if self.fuel is not None:
            data[Keyword("fuel")] = self.fuel
        return data


@dataclass(frozen=True)
class MemberRule:
    """One policy rule: on an event, gated by a pure predicate, act.

    ``on`` is the triggering event type; ``None`` marks a ``:boot`` rule
    that runs once after subscriptions are in place. ``when`` is an
    optional pure predicate (None = always true) evaluated under the
    unified member env scope ``{msg, event, self}`` (phase C). ``loop``
    when present makes this a cognitive rule: the engine runs the session
    first, then ``actions``; with no loop it is a deterministic rule that
    runs ``actions`` directly. Actions are collected as data
    (ActionRequest) and executed by the engine.

    ``fuel`` (phase D) is the rule's self-declared evaluation budget: for
    a deterministic rule the ``:when`` gate and the ``:actions`` share it
    as one combined counter; a cognitive rule's gate and completion
    actions are temporally separated, so each phase is capped at
    ``fuel``. ``None`` uses the engine defaults (when 200, actions 1000).
    Bounded above by :data:`tlon.agents.eval.MAX_FUEL` — a spec exceeding
    it is rejected at load (total semantics).
    """

    name: str
    on: EventType | None
    when: Form | None
    loop: "MemberLoop | None"
    actions: tuple[Form, ...]
    fuel: int | None = None

    # ── Serialization ──

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict (snake_case keys)."""
        return {
            "name": self.name,
            "on": _on_to_str(self.on),
            "when": dumps(self.when) if self.when is not None else None,
            "loop": self.loop.to_dict() if self.loop is not None else None,
            "actions": [dumps(form) for form in self.actions],
            "fuel": self.fuel,
        }

    def to_edn_data(self) -> dict[Keyword, Any]:
        """The EDN-native form (kebab-case keywords, Form values)."""
        data: dict[Keyword, Any] = {
            Keyword("name"): self.name,
            Keyword("on"): (
                Keyword("boot") if self.on is None else Keyword(_on_to_str(self.on))
            ),
            Keyword("actions"): self.actions,
        }
        if self.when is not None:
            data[Keyword("when")] = self.when
        if self.loop is not None:
            data[Keyword("loop")] = self.loop.to_edn_data()
        if self.fuel is not None:
            data[Keyword("fuel")] = self.fuel
        return data


@dataclass(frozen=True)
class MemberSpec:
    """Complete unified behavior spec for one member (semantics authority).

    ``source_hash`` is provenance, not semantics: the sha256 short hash of
    the behavior file the spec was loaded from. The loader computes it for
    every member (phase C, previously react-only). It is serialized for
    observability events but never written back into a behavior file.
    """

    version: int
    rules: tuple[MemberRule, ...]
    source_hash: str | None = None

    # ── Factories ──

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict (snake_case keys)."""
        return {
            "version": self.version,
            "source_hash": self.source_hash,
            "rules": [rule.to_dict() for rule in self.rules],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MemberSpec":
        """Build from a dict (snake_case keys) — structural validation.

        Total semantics, mirroring the legacy specs: missing required fields
        are errors, not defaults. Unknown keys are ignored with a warning
        (forward compatibility). The ``:when`` / ``:until`` / stage and
        section expressions are only shape-checked here — builtin-scope
        validation happens in :func:`validate_member_spec` (called by the
        loader, never by this factory, so roundtrips of host-dependent
        deterministic rules are not rejected).
        """
        _warn_unknown_keys(data, "top")
        version = _read(data, "version", path=":version", kind=int)
        if version != MEMBER_SPEC_VERSION:
            raise MemberSpecValidationError(
                f"unsupported :version {version!r}; "
                f"only version {MEMBER_SPEC_VERSION} is supported"
            )
        raw_rules = _require(data, "rules", ":rules")
        if isinstance(raw_rules, Form) or not isinstance(raw_rules, (list, tuple)):
            raise MemberSpecValidationError(
                f":rules must be a vector, got {_type_name(raw_rules)}: {raw_rules!r}"
            )
        if not raw_rules:
            raise MemberSpecValidationError(":rules must not be empty")
        rules = tuple(
            _rule_from(item, str(index)) for index, item in enumerate(raw_rules)
        )
        return cls(
            version=MEMBER_SPEC_VERSION,
            source_hash=_read_optional_str(data, "source_hash", ":source-hash"),
            rules=rules,
        )

    @classmethod
    def from_edn(cls, data: Any) -> "MemberSpec":
        """Build from a parsed EDN map (kebab-case keyword keys)."""
        if not isinstance(data, dict):
            raise MemberSpecValidationError(
                f"spec must contain a top-level map, got {_type_name(data)}: {data!r}"
            )
        return cls.from_dict(_normalize_data(data))

    def to_edn(self) -> str:
        """Serialize to EDN source text (kebab-case keywords)."""
        return dumps(self.to_edn_data())

    def to_edn_data(self) -> dict[Keyword, Any]:
        """The EDN-native form of the whole spec.

        ``:source-hash`` is emitted only when set — the write side must
        not serialize provenance into a behavior file (phase C).
        """
        data: dict[Keyword, Any] = {
            Keyword("version"): self.version,
            Keyword("rules"): [rule.to_edn_data() for rule in self.rules],
        }
        if self.source_hash is not None:
            data[Keyword("source-hash")] = self.source_hash
        return data


# ── Single validator (phase B; unified scopes in phase C) ──


def validate_member_spec(
    member: MemberSpec, *, builtins: dict[str, Builtin] | None = None
) -> MemberSpec:
    """Statically validate a MemberSpec at load time (no execution).

    Every expression field of every rule is checked against the unified
    member vocabulary (phase C): ``:when`` validates against the pure
    subset of the member registry under the unified env scope
    ``{msg, event, self}``; actions validate against
    :func:`~tlon.agents.eval.validate_member_action` under
    ``{msg, event, result, self}``; a cognitive rule's sections, stages
    and ``:until`` validate against the whitelisted names / loop
    builtins. The single scope superset covers both former runtimes —
    no existing spec references a symbol the old scopes rejected (D6.3).

    ``builtins`` is the registry to validate against (host predicates
    included, e.g. Adam's ``sleeping?``); it defaults to the host-less
    unified registry. This is the loader's gate, never
    ``from_dict``/``from_edn`` — the EDN front stays structural so that
    serialization roundtrips of host-dependent deterministic rules are
    not rejected. Returns ``member`` unchanged (usable as a chainable
    gate).
    """
    cognitive = [rule for rule in member.rules if rule.loop is not None]
    if len(cognitive) > 1:
        raise MemberSpecValidationError(
            "at most one cognitive rule per member (D6.7): "
            f"spec declares {len(cognitive)} rules with :loop — "
            "the cognitive engine binds to a single rule"
        )
    registry = builtins or make_member_builtins()
    pure = {name: builtin for name, builtin in registry.items() if builtin.pure}
    for rule in member.rules:
        label = f"rule {rule.name!r}"
        _validate_when(rule, label, pure)
        if rule.loop is not None:
            _validate_cognitive(rule, label)
        _validate_actions(rule, label, pure)
        _validate_fuel(rule, label)
    return member


def _validate_fuel(rule: MemberRule, label: str) -> None:
    """Total-semantics fuel checks (phase D): positive, under the engine cap.

    ``None`` means "use the engine defaults" and passes. A rule-level
    ``:fuel`` or a loop-level ``:loop/:fuel`` that is non-positive or
    exceeds :data:`~tlon.agents.eval.MAX_FUEL` rejects the spec at load.
    """
    if rule.fuel is not None:
        if rule.fuel <= 0:
            raise MemberSpecValidationError(
                f"{label}: :fuel must be a positive int, got {rule.fuel}"
            )
        if rule.fuel > MAX_FUEL:
            raise MemberSpecValidationError(
                f"{label}: :fuel {rule.fuel} exceeds engine hard cap {MAX_FUEL}"
            )
    if rule.loop is not None and rule.loop.fuel is not None:
        if rule.loop.fuel <= 0:
            raise MemberSpecValidationError(
                f"{label}: :loop/:fuel must be a positive int, got {rule.loop.fuel}"
            )
        if rule.loop.fuel > MAX_FUEL:
            raise MemberSpecValidationError(
                f"{label}: :loop/:fuel {rule.loop.fuel} exceeds engine hard cap {MAX_FUEL}"
            )


def member_type(spec: MemberSpec) -> str:
    """Derive a member's current cognitive form from its spec (observed
    value, ADR 0006 — never a stored category).

    A spec with at least one rule carrying a loop yields ``"react"``
    (the cognitive engine is attached); one with only loop-less rules
    yields ``"script"``. This is a projection of the spec's rules, not a
    member class: any member can cross between forms by changing its
    behavior file (via prototype instantiation), and a ``"script"``
    member is simply one whose spec has no loop rule today.
    """
    return "react" if any(rule.loop is not None for rule in spec.rules) else "script"


def _validate_when(rule: MemberRule, label: str, pure: dict[str, Builtin]) -> None:
    """Validate ``:when`` against the pure subset, unified env scope."""
    if rule.when is None:
        return
    try:
        validate_form(rule.when, pure, scope={"msg", "event", "self"})
    except EvalError as e:
        raise MemberSpecValidationError(
            f"{label}: invalid :when expression: {e}"
        ) from e


def _validate_actions(rule: MemberRule, label: str, pure: dict[str, Builtin]) -> None:
    """Validate every action form against the unified action vocabulary."""
    for form in rule.actions:
        try:
            validate_member_action(form, pure=pure, scope={"msg", "event", "result", "self"})
        except EvalError as e:
            raise MemberSpecValidationError(f"{label}: invalid action: {e}") from e


def _validate_cognitive(rule: MemberRule, label: str) -> None:
    """Validate a cognitive rule's loop: sections, stages, until."""
    loop = rule.loop
    assert loop is not None
    _validate_sections(loop.context_sections, label)
    _validate_stages(loop.stages, label)
    try:
        validate_form(loop.until, LOOP_BUILTINS, scope=set())
    except EvalError as e:
        raise MemberSpecValidationError(
            f"{label}: invalid :loop/:until expression: {e}"
        ) from e


def _validate_sections(sections: tuple[Form, ...], label: str) -> None:
    """Validate the section vector (mirrored the legacy AgentSpec's section checks)."""
    seen: set[str] = set()
    for form in sections:
        if not isinstance(form, Form) or not form:
            raise MemberSpecValidationError(
                f"{label}: :sections items must be section forms like (persona), "
                f"got {_type_name(form)}: {form!r}"
            )
        head = form[0]
        if not isinstance(head, Symbol):
            raise MemberSpecValidationError(
                f"{label}: :sections form head must be a symbol, got {dumps(head)}"
            )
        name = str(head)
        if name not in SECTION_NAMES:
            raise MemberSpecValidationError(
                f"{label}: unknown section {name!r} in :sections "
                f"(allowed: {sorted(SECTION_NAMES)})"
            )
        if name in seen:
            raise MemberSpecValidationError(
                f"{label}: duplicate section {name!r} in :sections"
            )
        seen.add(name)
        if name == "mail-history":
            if len(form) != 2:
                raise MemberSpecValidationError(
                    f"{label}: section 'mail-history' takes exactly one count "
                    f"argument, got {dumps(form)}"
                )
            count = form[1]
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                raise MemberSpecValidationError(
                    f"{label}: section 'mail-history' count must be a "
                    f"non-negative int, got {dumps(count)}"
                )
        elif len(form) != 1:
            raise MemberSpecValidationError(
                f"{label}: section {name!r} takes no arguments, got {dumps(form)}"
            )


def _validate_stages(stages: tuple[Form, ...], label: str) -> None:
    """Validate the stage vector (mirrored the legacy AgentSpec's stage checks)."""
    if not stages:
        raise MemberSpecValidationError(
            f"{label}: :stages must not be empty (no stage would ever advance the loop)"
        )
    for form in stages:
        if not isinstance(form, Form) or not form:
            raise MemberSpecValidationError(
                f"{label}: :stages items must be stage forms like (think), "
                f"got {_type_name(form)}: {form!r}"
            )
        head = form[0]
        if not isinstance(head, Symbol):
            raise MemberSpecValidationError(
                f"{label}: :stages form head must be a symbol, got {dumps(head)}"
            )
        name = str(head)
        if name not in STAGE_NAMES:
            raise MemberSpecValidationError(
                f"{label}: unknown stage {name!r} in :stages "
                f"(allowed: {sorted(STAGE_NAMES)})"
            )
        if len(form) > 2:
            raise MemberSpecValidationError(
                f"{label}: stage {name!r} takes at most one opts map, got {dumps(form)}"
            )
        if len(form) == 2:
            opts = form[1]
            if isinstance(opts, Form) or not isinstance(opts, dict):
                raise MemberSpecValidationError(
                    f"{label}: stage {name!r} opts must be a map, got {dumps(opts)}"
                )
            _validate_stage_opts(name, opts, form, label)


def _validate_stage_opts(
    name: str, opts: dict[Any, Any], form: Form, label: str
) -> None:
    """Validate one stage's opts map (mirrored the legacy AgentSpec's phase 7c checks)."""
    allowed = {"if", "max-steps"} if name == "plan" else {"if"}
    for key, value in opts.items():
        if not isinstance(key, Keyword):
            raise MemberSpecValidationError(
                f"{label}: stage {name!r} opts keys must be keywords, "
                f"got {_type_name(key)}: {key!r}"
            )
        if str(key) not in allowed:
            raise MemberSpecValidationError(
                f"{label}: stage {name!r}: unknown opts key :{key} in {dumps(form)}"
            )
        if str(key) == "if":
            try:
                validate_form(value, STAGE_COND_BUILTINS, scope=set())
            except EvalError as e:
                raise MemberSpecValidationError(
                    f"{label}: stage {name!r} :if: {e}"
                ) from e
        elif not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise MemberSpecValidationError(
                f"{label}: stage 'plan' :max-steps must be a positive int, "
                f"got {dumps(value)}"
            )


# ── Normalization (EDN keyword keys → snake_case strings) ──


def _normalize_data(value: Any) -> Any:
    """Recursively normalize EDN data: map keys become snake_case strings.

    Forms (``(...)``) are expressions, not data — they pass through
    untouched so their keywords/symbols keep their literal names.
    """
    if isinstance(value, Form):
        return value
    if isinstance(value, dict):
        return {
            _normalize_key(key): _normalize_data(item) for key, item in value.items()
        }
    if isinstance(value, list):
        return [_normalize_data(item) for item in value]
    return value


def _normalize_key(key: Any) -> str:
    """Map an EDN key (``:max-iterations``) to a snake_case field name."""
    if isinstance(key, (Keyword, str)):
        return str(key).replace("-", "_")
    raise MemberSpecValidationError(
        f"spec keys must be keywords, got {_type_name(key)}: {key!r}"
    )


# ── Field readers / validators ──


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


def _require(data: dict[str, Any], key: str, path: str) -> Any:
    """Read a required key or fail validation (total semantics)."""
    if key not in data:
        raise MemberSpecValidationError(f"missing required {path}")
    return data[key]


def _read(data: dict[str, Any], key: str, path: str, kind: type) -> Any:
    """Read a required scalar field, validating its type."""
    value = _require(data, key, path)
    if kind is bool:
        if not isinstance(value, bool):
            raise MemberSpecValidationError(
                f"{path} must be a bool, got {_type_name(value)}: {value!r}"
            )
        return value
    if kind is str:
        if not isinstance(value, str):
            raise MemberSpecValidationError(
                f"{path} must be a string, got {_type_name(value)}: {value!r}"
            )
        return value
    # int (bool excluded: it is an int subclass but never valid here)
    if not isinstance(value, int) or isinstance(value, bool):
        raise MemberSpecValidationError(
            f"{path} must be an int, got {_type_name(value)}: {value!r}"
        )
    return value


def _read_optional_int(data: dict[str, Any], key: str, path: str) -> int | None:
    """Read an optional int field; missing or nil yields None."""
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise MemberSpecValidationError(
            f"{path} must be an int, got {_type_name(value)}: {value!r}"
        )
    return value


def _read_optional_str(data: dict[str, Any], key: str, path: str) -> str | None:
    """Read an optional string field; missing or nil yields None."""
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise MemberSpecValidationError(
            f"{path} must be a string, got {_type_name(value)}: {value!r}"
        )
    return value


def _warn_unknown_keys(raw: dict[str, Any], section: str) -> None:
    known = _KNOWN_KEYS[section]
    for key in raw:
        if key not in known:
            logger.warning("member_spec_unknown_key", section=section, key=key)


_KNOWN_KEYS: dict[str, frozenset[str]] = {
    "top": frozenset({"version", "source_hash", "rules"}),
    "rule": frozenset({"name", "on", "when", "loop", "actions", "fuel"}),
    "loop": frozenset(
        {
            "context",
            "stages",
            "until",
            "max_iterations",
            "max_consecutive_errors",
            "reflect",
            "fuel",
        }
    ),
    "context": frozenset({"sections"}),
    "reflect": frozenset({"enabled", "interval_minutes"}),
}


def _rule_from(item: Any, rule_name: str) -> MemberRule:
    if isinstance(item, Form) or not isinstance(item, dict):
        raise MemberSpecValidationError(
            f"rule {rule_name!r}: must be a map, got {_type_name(item)}: {item!r}"
        )
    _warn_unknown_keys(item, "rule")
    return MemberRule(
        name=_read(item, "name", path=f"rule {rule_name!r}: :name", kind=str),
        on=_on_from(_require(item, "on", f"rule {rule_name!r}: :on"), rule_name),
        when=_expr_from(item.get("when"), ":when", rule_name, required=False),
        loop=_loop_from(item.get("loop"), rule_name),
        actions=_actions_from(item.get("actions"), rule_name),
        fuel=_read_optional_int(item, "fuel", f"rule {rule_name!r}: :fuel"),
    )


def _on_from(value: Any, rule_name: str) -> EventType | None:
    """Map an ``:on`` keyword to an EventType; ``:boot``/nil becomes None."""
    if value is None or (isinstance(value, str) and str(value) == "boot"):
        return None
    if not isinstance(value, str):
        raise MemberSpecValidationError(
            f"rule {rule_name!r}: :on must be an event keyword, "
            f"got {_type_name(value)}: {value!r}"
        )
    event_name = str(value).replace("-", "_").upper()
    try:
        return EventType[event_name]
    except KeyError:
        raise MemberSpecValidationError(
            f"rule {rule_name!r}: unknown event :{value} in :on"
        ) from None


def _on_to_str(on: EventType | None) -> str | None:
    """Serialize an EventType to its EDN keyword name; None = :boot."""
    if on is None:
        return None
    return on.name.lower().replace("_", "-")


def _expr_from(
    value: Any, path: str, rule_name: str, *, required: bool
) -> "Form | None":
    """Read an expression field: a parsed Form or an EDN source string.

    Structural shape only — builtin-scope validation is done by
    ``validate_member_spec`` in this module.
    """
    if value is None:
        if required:
            raise MemberSpecValidationError(f"rule {rule_name!r}: missing required {path}")
        return None
    if isinstance(value, Form):
        return value
    if isinstance(value, str):
        try:
            parsed = loads(value)
        except EDNParseError as e:
            raise MemberSpecValidationError(
                f"rule {rule_name!r}: invalid {path} expression: {e}"
            ) from e
        if not isinstance(parsed, Form):
            raise MemberSpecValidationError(
                f"rule {rule_name!r}: {path} must be an expression, "
                f"got {_type_name(parsed)}: {parsed!r}"
            )
        return parsed
    raise MemberSpecValidationError(
        f"rule {rule_name!r}: {path} must be an expression, "
        f"got {_type_name(value)}: {value!r}"
    )


def _required_expr(value: Any, path: str, rule_name: str) -> Form:
    """Read a required expression field (never None)."""
    form = _expr_from(value, path, rule_name, required=True)
    assert form is not None
    return form


def _forms_from(value: Any, path: str, rule_name: str) -> tuple[Form, ...]:
    """Read a vector of expression forms (parsed Forms or EDN strings)."""
    if isinstance(value, Form) or not isinstance(value, (list, tuple)):
        raise MemberSpecValidationError(
            f"rule {rule_name!r}: {path} must be a vector, "
            f"got {_type_name(value)}: {value!r}"
        )
    forms: list[Form] = []
    for item in value:
        form = item
        if type(form) is str:
            try:
                form = loads(form)
            except EDNParseError as e:
                raise MemberSpecValidationError(
                    f"rule {rule_name!r}: invalid {path} expression: {e}"
                ) from e
        if not isinstance(form, Form) or not form:
            raise MemberSpecValidationError(
                f"rule {rule_name!r}: {path} items must be forms like (think), "
                f"got {_type_name(item)}: {item!r}"
            )
        forms.append(form)
    return tuple(forms)


def _loop_from(value: Any, rule_name: str) -> "MemberLoop | None":
    """Optional :loop section — the cognitive session (None = deterministic)."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise MemberSpecValidationError(
            f"rule {rule_name!r}: :loop must be a map, got {_type_name(value)}: {value!r}"
        )
    _warn_unknown_keys(value, "loop")
    raw_context = _require(value, "context", f"rule {rule_name!r}: :loop/:context")
    if not isinstance(raw_context, dict):
        raise MemberSpecValidationError(
            f"rule {rule_name!r}: :loop/:context must be a map, "
            f"got {_type_name(raw_context)}: {raw_context!r}"
        )
    _warn_unknown_keys(raw_context, "context")
    return MemberLoop(
        context_sections=_forms_from(
            _require(
                raw_context,
                "sections",
                f"rule {rule_name!r}: :loop/:context/:sections",
            ),
            ":loop/:context/:sections",
            rule_name,
        ),
        stages=_forms_from(
            _require(value, "stages", f"rule {rule_name!r}: :loop/:stages"),
            ":loop/:stages",
            rule_name,
        ),
        until=_required_expr(value.get("until"), ":loop/:until", rule_name),
        max_iterations=_read(
            value,
            "max_iterations",
            f"rule {rule_name!r}: :loop/:max-iterations",
            kind=int,
        ),
        max_consecutive_errors=_read(
            value,
            "max_consecutive_errors",
            f"rule {rule_name!r}: :loop/:max-consecutive-errors",
            kind=int,
        ),
        reflect=_reflect_from(
            _require(value, "reflect", f"rule {rule_name!r}: :loop/:reflect"),
            rule_name,
        ),
        fuel=_read_optional_int(value, "fuel", f"rule {rule_name!r}: :loop/:fuel"),
    )


def _reflect_from(value: Any, rule_name: str) -> ReflectSpec:
    if not isinstance(value, dict):
        raise MemberSpecValidationError(
            f"rule {rule_name!r}: :loop/:reflect must be a map, "
            f"got {_type_name(value)}: {value!r}"
        )
    _warn_unknown_keys(value, "reflect")
    return ReflectSpec(
        enabled=_read(
            value, "enabled", f"rule {rule_name!r}: :loop/:reflect/:enabled", kind=bool
        ),
        interval_minutes=_read(
            value,
            "interval_minutes",
            f"rule {rule_name!r}: :loop/:reflect/:interval-minutes",
            kind=int,
        ),
    )


def _actions_from(value: Any, rule_name: str) -> tuple[Form, ...]:
    """Read the optional :actions vector; every item a non-empty form.

    Shape only — action-head whitelist and argument purity are the single
    validator's job (:func:`validate_member_spec`), not the EDN front's.
    """
    if value is None:
        return ()
    if isinstance(value, Form) or not isinstance(value, (list, tuple)):
        raise MemberSpecValidationError(
            f"rule {rule_name!r}: :actions must be a vector, "
            f"got {_type_name(value)}: {value!r}"
        )
    forms: list[Form] = []
    for item in value:
        form = item
        if type(form) is str:
            try:
                form = loads(form)
            except EDNParseError as e:
                raise MemberSpecValidationError(
                    f"rule {rule_name!r}: invalid :actions expression: {e}"
                ) from e
        if not isinstance(form, Form) or not form:
            raise MemberSpecValidationError(
                f"rule {rule_name!r}: :actions items must be action forms, "
                f"got {_type_name(item)}: {item!r}"
            )
        forms.append(form)
    return tuple(forms)
