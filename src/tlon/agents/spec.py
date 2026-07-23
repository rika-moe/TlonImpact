"""AgentSpec — the single semantic authority for agent behavior.

Per the DSL design (docs/design/dsl-agents.md), agent behavior is data,
not code. ``AgentSpec`` is a typed, pure-data dataclass tree; the EDN
behavior file (``~/.tlon/{agent_id}/config/behavior.tlon``) is only a
serialization frontend that parses into it.

Boundaries:

- ``AgentConfig.proactive`` / ``max_iterations`` / ``max_consecutive_errors``
  remain as the data source for :meth:`AgentSpec.default` (and as the
  compatibility path when no spec file exists). ``session_timeout_seconds``
  and ``message_queue_size`` are construction-time infrastructure and stay
  in ``AgentConfig`` — they never enter the spec.
- The message router pre-filters proactive routing via
  ``AgentConfig.proactive`` (router.py) *before* the spec exists in the
  dispatch path; ``spec.trigger.proactive`` is the second gate inside the
  agent. ``AgentSpec.default`` derives from the same config field, so the
  two gates agree and behavior is unchanged.
- The no-tool reminder in LoopDriver currently has no on/off switch, so
  v1 has no ``no_tool_reminder`` field — the reminder stays unconditional.

Missing keys in a spec file fall back to a base spec (``DefaultSpec``
derived from the agent's config), so a partial file is a pure override.
Unknown keys are ignored with a warning for forward compatibility.
"""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from tlon.agents.edn import EDNParseError, Form, Keyword, dumps, loads
from tlon.agents.eval import TRIGGER_BUILTINS, EvalError, validate_form
from tlon.base.logger import get_logger
from tlon.base.paths import paths

if TYPE_CHECKING:
    from tlon.agents.agent import Agent

logger = get_logger(__name__)

SPEC_VERSION = 1
BEHAVIOR_FILE_NAME = "behavior.tlon"

_LOOP_MODES = ("react",)
_TOOL_MODES = ("background",)


class SpecValidationError(ValueError):
    """An EDN spec parsed successfully but failed semantic validation."""


@dataclass(frozen=True)
class TriggerSpec:
    """When the agent fires on an inbound message.

    ``when`` is an optional L1 trigger expression (a parsed EDN Form,
    statically validated at load time against ``TRIGGER_BUILTINS``). When
    set, it takes precedence over the data fields below; when ``None``,
    the declarative data fields apply.
    """

    on_mention: bool = True  # always respond when @-mentioned
    proactive: bool = False  # respond to unaddressed human messages
    when: "Form | None" = None  # L1 expression mode (phase 3a)


@dataclass(frozen=True)
class ContextSpec:
    """What goes into the LLM context and system prompt."""

    persona: bool = True  # IDENTITY/SOUL/RULES + team SOUL sections
    protocol: bool = True  # framework interaction-protocol section
    knowledge: bool = True  # memory knowledge blocks injection
    room_history: int = 20  # room history message count; 0 disables


@dataclass(frozen=True)
class LoopSpec:
    """Shape and limits of the cognitive loop (engine-executed)."""

    mode: str = "react"  # v1 only :react
    max_iterations: int = 10
    tools: str = "background"  # v1 only :background
    max_consecutive_errors: int = 3


@dataclass(frozen=True)
class ReflectSpec:
    """Periodic background memory audit."""

    enabled: bool = True
    interval_minutes: int = 15


@dataclass(frozen=True)
class OnCompleteSpec:
    """What happens after a session completes."""

    save_interaction: bool = True
    reflect: ReflectSpec = field(default_factory=ReflectSpec)


@dataclass(frozen=True)
class AgentSpec:
    """Complete behavior spec for one agent (semantics authority).

    ``source_hash`` is provenance, not semantics: the sha256 short hash (12
    hex chars) of the behavior file this spec was loaded from, or
    ``"default"`` when running on DefaultSpec (no file, or a broken file
    that fell back). It is stamped onto session spans so any trace can be
    tied back to the exact spec version in effect.
    """

    version: int = SPEC_VERSION
    trigger: TriggerSpec = field(default_factory=TriggerSpec)
    context: ContextSpec = field(default_factory=ContextSpec)
    loop: LoopSpec = field(default_factory=LoopSpec)
    on_complete: OnCompleteSpec = field(default_factory=OnCompleteSpec)
    source_hash: str = "default"

    # ── Factories ──

    @classmethod
    def default(cls, agent: "Agent") -> "AgentSpec":
        """Replicate current behavior exactly from agent + memory config."""
        memory_config = getattr(agent.memory, "config", None)
        return cls(
            version=SPEC_VERSION,
            trigger=TriggerSpec(on_mention=True, proactive=agent.config.proactive),
            context=ContextSpec(),
            loop=LoopSpec(
                max_iterations=agent.config.max_iterations,
                max_consecutive_errors=agent.config.max_consecutive_errors,
            ),
            on_complete=OnCompleteSpec(
                save_interaction=True,
                reflect=ReflectSpec(
                    enabled=getattr(memory_config, "reflection_enabled", True),
                    interval_minutes=getattr(memory_config, "reflection_interval_minutes", 15),
                ),
            ),
        )

    @classmethod
    def load(cls, agent: "Agent") -> "AgentSpec":
        """Load the spec for an agent: behavior file, else DefaultSpec.

        ``~/.tlon/{agent_id}/config/behavior.tlon`` is parsed when it
        exists; parse/validation failures log a warning and fall back to
        DefaultSpec so a broken file never prevents the agent from booting.
        """
        base = cls.default(agent)
        behavior_file = paths.agent_behavior_file(agent.config.id)
        if not behavior_file.exists():
            return base
        try:
            raw = behavior_file.read_text(encoding="utf-8")
            spec = cls.from_edn(loads(raw), base=base)
        except (EDNParseError, SpecValidationError, OSError) as e:
            logger.warning("agent_spec_load_failed", path=str(behavior_file), error=str(e))
            return base
        source_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
        spec = replace(spec, source_hash=source_hash)
        logger.info("agent_spec_loaded", path=str(behavior_file), spec_hash=source_hash)
        return spec

    # ── Serialization ──

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict (snake_case keys)."""
        return {
            "version": self.version,
            "trigger": {
                "on_mention": self.trigger.on_mention,
                "proactive": self.trigger.proactive,
                "when": dumps(self.trigger.when) if self.trigger.when is not None else None,
            },
            "context": {
                "persona": self.context.persona,
                "protocol": self.context.protocol,
                "knowledge": self.context.knowledge,
                "room_history": self.context.room_history,
            },
            "loop": {
                "mode": self.loop.mode,
                "max_iterations": self.loop.max_iterations,
                "tools": self.loop.tools,
                "max_consecutive_errors": self.loop.max_consecutive_errors,
            },
            "on_complete": {
                "save_interaction": self.on_complete.save_interaction,
                "reflect": {
                    "enabled": self.on_complete.reflect.enabled,
                    "interval_minutes": self.on_complete.reflect.interval_minutes,
                },
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], base: "AgentSpec | None" = None) -> "AgentSpec":
        """Build a spec from a dict (snake_case keys), merging over ``base``.

        Missing keys inherit from ``base`` (or the built-in defaults when
        no base is given). Unknown keys are ignored with a warning.
        """
        base = base or cls()
        _warn_unknown_keys(data, "top")
        if "version" not in data:
            raise SpecValidationError("missing required :version (only version 1 is supported)")
        version = _read(data, "version", base.version, path=":version", kind=int)
        if version != SPEC_VERSION:
            raise SpecValidationError(
                f"unsupported :version {version!r}; only version {SPEC_VERSION} is supported"
            )
        return cls(
            version=SPEC_VERSION,
            trigger=_trigger_section(data, base.trigger),
            context=_section(data, "context", base.context, _context_from),
            loop=_section(data, "loop", base.loop, _loop_from),
            on_complete=_section(data, "on_complete", base.on_complete, _on_complete_from),
        )

    @classmethod
    def from_edn(cls, data: Any, base: "AgentSpec | None" = None) -> "AgentSpec":
        """Build a spec from a parsed EDN map (kebab-case keyword keys)."""
        if not isinstance(data, dict):
            raise SpecValidationError(
                f"spec file must contain a top-level map, got {_type_name(data)}"
            )
        return cls.from_dict(_normalize_data(data), base=base)


def _normalize_data(value: Any) -> Any:
    """Recursively normalize EDN data: map keys become snake_case strings.

    Forms (``(...)``) are expressions, not data — they pass through
    untouched so their keywords/symbols keep their literal names.
    """
    if isinstance(value, Form):
        return value
    if isinstance(value, dict):
        return {_normalize_key(key): _normalize_data(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_data(item) for item in value]
    return value


# ── Field readers / validators ──


def _normalize_key(key: Any) -> str:
    """Map an EDN key (``:on-mention``) to a snake_case field name."""
    if isinstance(key, (Keyword, str)):
        return str(key).replace("-", "_")
    raise SpecValidationError(f"spec keys must be keywords, got {_type_name(key)}: {key!r}")


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


def _read(data: dict[str, Any], key: str, default: Any, path: str, kind: type) -> Any:
    """Read a scalar field, validating its type when present."""
    if key not in data:
        return default
    value = data[key]
    if kind is bool:
        if not isinstance(value, bool):
            raise SpecValidationError(
                f"{path} must be a bool, got {_type_name(value)}: {value!r}"
            )
        return value
    # int (bool excluded: it is an int subclass but never valid here)
    if not isinstance(value, int) or isinstance(value, bool):
        raise SpecValidationError(f"{path} must be an int, got {_type_name(value)}: {value!r}")
    return value


def _read_enum(data: dict[str, Any], key: str, default: str, path: str, allowed: tuple[str, ...]) -> str:
    if key not in data:
        return default
    value = data[key]
    if not isinstance(value, (Keyword, str)):
        raise SpecValidationError(
            f"{path} must be a keyword, got {_type_name(value)}: {value!r}"
        )
    name = str(value)
    if name not in allowed:
        raise SpecValidationError(
            f"{path} must be one of {list(allowed)}, got {name!r}"
        )
    return name


def _section(
    data: dict[str, Any],
    key: str,
    base: Any,
    builder: "Callable[[dict[str, Any], Any], Any]",
) -> Any:
    raw = data.get(key)
    if raw is None:
        return base
    if not isinstance(raw, dict):
        raise SpecValidationError(
            f":{key.replace('_', '-')} must be a map, got {_type_name(raw)}: {raw!r}"
        )
    _warn_unknown_keys(raw, key)
    return builder(raw, base)


def _warn_unknown_keys(raw: dict[str, Any], section: str) -> None:
    known = _KNOWN_KEYS[section]
    for key in raw:
        if key not in known:
            logger.warning("agent_spec_unknown_key", section=section, key=key)


_KNOWN_KEYS: dict[str, frozenset[str]] = {
    "top": frozenset({"version", "trigger", "context", "loop", "on_complete"}),
    "trigger": frozenset({"on_mention", "proactive", "when"}),
    "context": frozenset({"persona", "protocol", "knowledge", "room_history"}),
    "loop": frozenset({"mode", "max_iterations", "tools", "max_consecutive_errors"}),
    "on_complete": frozenset({"save_interaction", "reflect"}),
    "reflect": frozenset({"enabled", "interval_minutes"}),
}


def _trigger_from(data: dict[str, Any], base: TriggerSpec) -> TriggerSpec:
    return TriggerSpec(
        on_mention=_read(
            data, "on_mention", base.on_mention, path=":trigger/:on-mention", kind=bool
        ),
        proactive=_read(
            data, "proactive", base.proactive, path=":trigger/:proactive", kind=bool
        ),
        when=_when_from(data.get("when"), base.when),
    )


def _when_from(value: Any, default: "Form | None") -> "Form | None":
    """Read an optional trigger expression from the dict path.

    Accepts a parsed Form, an EDN source string (``to_dict`` output), or
    None. Anything else is a validation error.
    """
    if value is None:
        return default
    if isinstance(value, Form):
        return _validate_trigger_expr(value)
    if isinstance(value, str):
        try:
            parsed = loads(value)
        except EDNParseError as e:
            raise SpecValidationError(f"invalid :trigger/:when expression: {e}") from e
        if not isinstance(parsed, Form):
            raise SpecValidationError(
                f":trigger/:when must be an expression, got {_type_name(parsed)}: {parsed!r}"
            )
        return _validate_trigger_expr(parsed)
    raise SpecValidationError(
        f":trigger/:when must be an expression string, got {_type_name(value)}: {value!r}"
    )


def _trigger_section(data: dict[str, Any], base: TriggerSpec) -> TriggerSpec:
    """Build the trigger section: data map (declarative) or Form (expression)."""
    raw = data.get("trigger")
    if raw is None:
        return base
    if isinstance(raw, Form):
        return replace(base, when=_validate_trigger_expr(raw))
    if not isinstance(raw, dict):
        raise SpecValidationError(
            f":trigger must be a map or an expression, got {_type_name(raw)}: {raw!r}"
        )
    _warn_unknown_keys(raw, "trigger")
    return _trigger_from(raw, base)


def _validate_trigger_expr(form: Form) -> Form:
    """Statically validate a trigger expression at load time (no execution).

    Only whitelisted special forms, registered pure builtins, and symbols
    the trigger environment provides (``msg`` / ``self``, plus ``let``
    bindings) are accepted — so ``writePrototype`` schema validation
    rejects bad expressions before they ever reach an agent.
    """
    try:
        validate_form(form, TRIGGER_BUILTINS, scope={"msg", "self"})
    except EvalError as e:
        raise SpecValidationError(f"invalid :trigger expression: {e}") from e
    return form


def _context_from(data: dict[str, Any], base: ContextSpec) -> ContextSpec:
    return ContextSpec(
        persona=_read(data, "persona", base.persona, path=":context/:persona", kind=bool),
        protocol=_read(data, "protocol", base.protocol, path=":context/:protocol", kind=bool),
        knowledge=_read(
            data, "knowledge", base.knowledge, path=":context/:knowledge", kind=bool
        ),
        room_history=_read(
            data, "room_history", base.room_history, path=":context/:room-history", kind=int
        ),
    )


def _loop_from(data: dict[str, Any], base: LoopSpec) -> LoopSpec:
    return LoopSpec(
        mode=_read_enum(data, "mode", base.mode, path=":loop/:mode", allowed=_LOOP_MODES),
        max_iterations=_read(
            data, "max_iterations", base.max_iterations, path=":loop/:max-iterations", kind=int
        ),
        tools=_read_enum(data, "tools", base.tools, path=":loop/:tools", allowed=_TOOL_MODES),
        max_consecutive_errors=_read(
            data,
            "max_consecutive_errors",
            base.max_consecutive_errors,
            path=":loop/:max-consecutive-errors",
            kind=int,
        ),
    )


def _on_complete_from(data: dict[str, Any], base: OnCompleteSpec) -> OnCompleteSpec:
    return OnCompleteSpec(
        save_interaction=_read(
            data,
            "save_interaction",
            base.save_interaction,
            path=":on-complete/:save-interaction",
            kind=bool,
        ),
        reflect=_section(data, "reflect", base.reflect, _reflect_from),
    )


def _reflect_from(data: dict[str, Any], base: ReflectSpec) -> ReflectSpec:
    return ReflectSpec(
        enabled=_read(
            data, "enabled", base.enabled, path=":on-complete/:reflect/:enabled", kind=bool
        ),
        interval_minutes=_read(
            data,
            "interval_minutes",
            base.interval_minutes,
            path=":on-complete/:reflect/:interval-minutes",
            kind=int,
        ),
    )
