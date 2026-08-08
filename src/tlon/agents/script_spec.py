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
"""

from dataclasses import dataclass
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


class ScriptSpecValidationError(ValueError):
    """A script spec failed EDN-level semantic validation."""


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
            spec = cls.from_edn(loads(raw))
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
