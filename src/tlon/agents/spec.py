"""AgentSpec — the single semantic authority for agent behavior.

Per the DSL design (docs/design/dsl-agents.md), agent behavior is data,
not code. ``AgentSpec`` is a typed, pure-data dataclass tree; the EDN
behavior file (``~/.tlon/{agent_id}/config/behavior.tlon``) is only a
serialization frontend that parses into it.

The spec is **total**: the behavior file is the whole behavior. A file
must declare every section (``:version``, ``:trigger``, ``:context``,
``:loop``, ``:on-complete``) and every required field inside them — there
is no partial merge, no base spec, and no ``DefaultSpec`` derivation from
``AgentConfig``. A missing, unparseable, or incomplete behavior file
refuses the agent's boot (:meth:`AgentSpec.load` raises); the registry
logs the failure and keeps the server and other agents running.

Boundaries:

- ``AgentConfig`` keeps construction-time infrastructure only
  (``session_timeout_seconds``, ``message_queue_size``) — those never
  enter the spec. ``AgentConfig.proactive`` is deprecated and ignored
  (proactive retired with the mail model, R1b).
- The no-tool reminder in LoopDriver currently has no on/off switch, so
  v1 has no ``no_tool_reminder`` field — the reminder stays unconditional.
- ``LoopSpec.stages`` / ``until`` (phase 7a) turn the loop into an
  interpreted program: a whitelisted stage vector plus a pure
  termination expression. The engine still owns iteration — including an
  unconditional ``max_iterations`` hard ceiling — per "the DSL never
  executes iteration".
- ``ContextSpec.sections`` (phase 7b) does the same for context
  assembly: a whitelisted section vector the engine interprets in
  order. Legacy boolean/count keys (``:persona`` / ``:protocol`` /
  ``:knowledge`` / ``:mail-history``) are retired — ``:sections`` is
  required and legacy keys warn as unknown keys.

Unknown keys are ignored with a warning for forward compatibility.
"""

import hashlib
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, NotRequired, TypedDict

from tlon.agents.edn import EDNParseError, Form, Keyword, Symbol, dumps, loads
from tlon.agents.eval import (
    LOOP_BUILTINS,
    STAGE_COND_BUILTINS,
    TRIGGER_BUILTINS,
    EvalError,
    validate_action_form,
    validate_form,
)
from tlon.base.logger import get_logger
from tlon.base.paths import paths

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)

SPEC_VERSION = 1
BEHAVIOR_FILE_NAME = "behavior.tlon"

#: Stage names the engine implements (phases 7a/7c). Each stage is a
#: Python-implemented, testable semantic unit; ``act`` means "execute
#: this round's tool calls — all of them", not a single tool call.
STAGE_NAMES = frozenset({"think", "act", "plan", "critique", "revise"})

#: The standard ReAct program: the canonical stage vector and until
#: expression, used by the wizard template and tests. These are building
#: blocks for authoring a spec file, never implicit defaults — every
#: behavior file must spell its program out in full.
REACT_STAGES: tuple[Form, ...] = (loads("(think)"), loads("(act)"))
REACT_UNTIL: Form = loads("(or (iteration-limit-reached?) (error-limit-reached?))")

#: Section names the engine implements (phase 7b). ``persona`` and
#: ``protocol`` feed the system prompt; ``knowledge`` and
#: ``mail-history`` feed the conversation context, in vector order.
SECTION_NAMES = frozenset({"persona", "protocol", "knowledge", "mail-history"})

#: The standard context assembly program (phase 7b): full persona and
#: protocol in the system prompt, then knowledge, then 20 mails of
#: history in the conversation. Building block for authoring a spec
#: file (wizard template, tests), never an implicit default.
DEFAULT_SECTIONS: tuple[Form, ...] = (
    loads("(persona)"),
    loads("(protocol)"),
    loads("(knowledge)"),
    loads("(mail-history 20)"),
)


class SpecValidationError(ValueError):
    """A behavior spec failed EDN-level semantic validation."""


@dataclass(frozen=True)
class TriggerSpec:
    """When the agent fires on an inbound mail.

    ``when`` is an optional L1 trigger expression (a parsed EDN Form,
    statically validated at load time against ``TRIGGER_BUILTINS``). When
    set, it takes precedence over the data field below; when ``None``,
    the declarative rule applies.
    """

    on_mention: bool  # respond when my address is in To or CC
    when: "Form | None" = None  # L1 expression mode (phase 3a)


@dataclass(frozen=True)
class ContextSpec:
    """What goes into the LLM context and system prompt (phase 7b).

    ``sections`` is the section vector: each form is ``(persona)``,
    ``(protocol)``, ``(knowledge)``, or ``(mail-history N)`` with a
    non-negative int count, statically validated at load time against
    :data:`SECTION_NAMES`. The engine interprets the vector in order;
    the current mail is always appended last by the engine, outside
    the DSL.
    """

    sections: tuple[Form, ...]


def has_section(sections: tuple[Form, ...], name: str) -> bool:
    """Whether the section vector contains a section named ``name``."""
    return any(str(form[0]) == name for form in sections)


@dataclass(frozen=True)
class LoopSpec:
    """Shape and limits of the cognitive loop (engine-executed).

    ``stages`` is the stage vector (phases 7a/7c): each form is ``(name)``
    or ``(name {opts})`` with ``name`` in :data:`STAGE_NAMES`, statically
    validated at load time. Every stage accepts an optional ``:if``
    condition expression (evaluated per round against loop state and the
    session scratchpad); ``plan`` additionally accepts ``:max-steps``.
    ``until`` is the loop termination expression,
    validated against ``LOOP_BUILTINS``. The engine interprets them —
    the DSL describes, the engine executes.
    """

    max_iterations: int
    max_consecutive_errors: int
    stages: tuple[Form, ...]
    until: Form


@dataclass(frozen=True)
class ReflectSpec:
    """Periodic background memory audit."""

    enabled: bool
    interval_minutes: int


@dataclass(frozen=True)
class OnCompleteSpec:
    """What happens after a session completes.

    ``actions`` holds parsed on-complete action forms (phase 4b): each is
    a ``(spawn ...)`` / ``(emit ...)`` form or ``(when cond action ...)``,
    statically validated at load time (:func:`validate_action_form`).
    Evaluation is pure (actions are collected as data); the engine
    executes them once when a session completes.
    """

    save_interaction: bool
    reflect: ReflectSpec
    actions: tuple[Form, ...] = ()  # phase 4b action expressions


@dataclass(frozen=True)
class AgentSpec:
    """Complete behavior spec for one agent (semantics authority).

    ``source_hash`` is provenance, not semantics: the sha256 short hash (12
    hex chars) of the behavior file this spec was loaded from. It is
    stamped onto session spans so any trace can be tied back to the exact
    spec version in effect. Specs built directly (validation-only paths,
    tests) carry the empty string — only :meth:`load` stamps a real hash.
    """

    version: int
    trigger: TriggerSpec
    context: ContextSpec
    loop: LoopSpec
    on_complete: OnCompleteSpec
    source_hash: str

    # ── Factories ──

    @classmethod
    def load(cls, agent_id: str) -> "AgentSpec":
        """Load an agent's spec from its behavior file — total semantics.

        ``~/.tlon/{agent_id}/config/behavior.tlon`` must exist, parse as
        EDN, and validate as a complete spec. Any failure raises: the
        agent refuses to boot (the registry logs and isolates the
        failure; the server and other agents are unaffected).

        Args:
            agent_id: id of the agent whose behavior file to load

        Raises:
            SpecValidationError: file missing, unreadable, or failed
                schema validation
            EDNParseError: file is not valid EDN
        """
        behavior_file = paths.agent_behavior_file(agent_id)
        if not behavior_file.exists():
            logger.error(
                "agent_spec_invalid",
                agent_id=agent_id,
                path=str(behavior_file),
                reason="behavior file missing",
            )
            raise SpecValidationError(
                f"behavior file missing, agent refused to boot: {behavior_file}"
            )
        try:
            raw = behavior_file.read_text(encoding="utf-8")
            spec = cls.from_edn(loads(raw))
        except (EDNParseError, SpecValidationError, OSError) as e:
            logger.error(
                "agent_spec_invalid",
                agent_id=agent_id,
                path=str(behavior_file),
                reason=str(e),
            )
            raise
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
                "when": dumps(self.trigger.when) if self.trigger.when is not None else None,
            },
            "context": {
                "sections": [dumps(form) for form in self.context.sections],
            },
            "loop": {
                "max_iterations": self.loop.max_iterations,
                "max_consecutive_errors": self.loop.max_consecutive_errors,
                "stages": [dumps(form) for form in self.loop.stages],
                "until": dumps(self.loop.until),
            },
            "on_complete": {
                "save_interaction": self.on_complete.save_interaction,
                "reflect": {
                    "enabled": self.on_complete.reflect.enabled,
                    "interval_minutes": self.on_complete.reflect.interval_minutes,
                },
                "actions": [dumps(form) for form in self.on_complete.actions],
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentSpec":
        """Build a spec from a dict (snake_case keys) — total validation.

        Every section and every required field must be present; a missing
        one is a validation error, not a default. Unknown keys are ignored
        with a warning.
        """
        _warn_unknown_keys(data, "top")
        version = _read(data, "version", path=":version", kind=int)
        if version != SPEC_VERSION:
            raise SpecValidationError(
                f"unsupported :version {version!r}; only version {SPEC_VERSION} is supported"
            )
        for section in ("trigger", "context", "loop", "on_complete"):
            if section not in data:
                raise SpecValidationError(
                    f"missing required :{section.replace('_', '-')} section "
                    "(a behavior spec is total: no partial files, no defaults)"
                )
        return cls(
            version=SPEC_VERSION,
            trigger=_trigger_section(data),
            context=_context_section(data),
            loop=_loop_section(data),
            on_complete=_on_complete_section(data),
            source_hash="",
        )

    @classmethod
    def from_edn(cls, data: Any) -> "AgentSpec":
        """Build a spec from a parsed EDN map (kebab-case keyword keys)."""
        if not isinstance(data, dict):
            raise SpecValidationError(
                f"spec file must contain a top-level map, got {_type_name(data)}"
            )
        return cls.from_dict(_normalize_data(data))


def standard_behavior_edn(agent_id: str | None = None) -> str:
    """The canonical complete behavior file (standard ReAct program).

    Used by the wizard when creating an agent: the file spells out every
    section in full (:func:`from_edn` accepts it), built from
    :data:`REACT_STAGES` / :data:`REACT_UNTIL` / :data:`DEFAULT_SECTIONS`.

    When ``agent_id`` matches a known resident, a personalized behavior is
    returned instead — this is the stage 7d dogfood path: exercise DSL
    features (trigger expressions, stage variety, section ordering,
    ``:if`` conditions, reflect tuning) on real agents.
    """
    if agent_id is not None:
        resident = _resident_behavior_edn(agent_id)
        if resident is not None:
            return resident

    data: dict[Any, Any] = {
        Keyword("version"): SPEC_VERSION,
        Keyword("trigger"): {Keyword("on-mention"): True},
        Keyword("context"): {Keyword("sections"): list(DEFAULT_SECTIONS)},
        Keyword("loop"): {
            Keyword("stages"): list(REACT_STAGES),
            Keyword("until"): REACT_UNTIL,
            Keyword("max-iterations"): 10,
            Keyword("max-consecutive-errors"): 3,
        },
        Keyword("on-complete"): {
            Keyword("save-interaction"): True,
            Keyword("reflect"): {
                Keyword("enabled"): True,
                Keyword("interval-minutes"): 15,
            },
        },
    }
    return dumps(data) + "\n"


# ── Resident behavior dogfood (stage 7d) ──

#: Known resident agent ids whose behavior.tlon gets a personalized DSL
#: program instead of the canonical ReAct default.
_RESIDENT_IDS = frozenset({
    "lin-zhiyao", "jiang-yubai", "shen-qingyan",
    "gu-yanxi", "su-wanqing", "xie-yunshu",
})


def _mk_spec(
    *,
    stages: str,
    max_iterations: int,
    sections: str,
    mail_history: int,
    reflect_minutes: int = 15,
    trigger_expr: str | None = None,
    plan_on_first_iteration: bool = False,
    max_consecutive_errors: int = 3,
) -> str:
    """Build a resident behavior EDN string from a compact specification.

    ``stages`` is a space-separated list of stage keywords (e.g.
    ``"think plan act critique revise"``). ``sections`` is a
    space-separated ordered list of section keywords (e.g.
    ``"persona protocol knowledge"``). ``trigger_expr``, when set, is a
    raw EDN expression form placed in the ``:when`` slot of the trigger
    map (expression-mode trigger). ``plan_on_first_iteration`` gates the
    ``plan`` stage behind ``:if (first-iteration?)`` (plan once, then
    execute — dogfood for the ``first-iteration?`` stage condition).
    """
    stage_map = {
        "think": "(think)",
        "act": "(act)",
        "plan": (
            "(plan {:if (first-iteration?)})"
            if plan_on_first_iteration
            else "(plan)"
        ),
        "critique": "(critique)",
        "revise": '(revise {:if (critique-failed?)})',
    }
    section_map = {
        "persona": "(persona)",
        "protocol": "(protocol)",
        "knowledge": "(knowledge)",
        "mail-history": f"(mail-history {mail_history})",
    }

    stage_forms = [stage_map[s] for s in stages.split()]
    section_forms = [section_map[s] for s in sections.split()]

    trigger: Any
    if trigger_expr is not None:
        trigger = {
            Keyword("on-mention"): True,
            Keyword("when"): loads(trigger_expr),
        }
    else:
        trigger = {Keyword("on-mention"): True}

    data: dict[Any, Any] = {
        Keyword("version"): SPEC_VERSION,
        Keyword("trigger"): trigger,
        Keyword("context"): {
            Keyword("sections"): [loads(f) for f in section_forms],
        },
        Keyword("loop"): {
            Keyword("stages"): [loads(f) for f in stage_forms],
            Keyword("until"): REACT_UNTIL,
            Keyword("max-iterations"): max_iterations,
            Keyword("max-consecutive-errors"): max_consecutive_errors,
        },
        Keyword("on-complete"): {
            Keyword("save-interaction"): True,
            Keyword("reflect"): {
                Keyword("enabled"): True,
                Keyword("interval-minutes"): reflect_minutes,
            },
        },
    }
    return dumps(data) + "\n"


class _ResidentSpec(TypedDict):
    """The compact per-resident behavior knobs fed to :func:`_mk_spec`."""

    stages: str
    max_iterations: int
    sections: str
    mail_history: int
    reflect_minutes: NotRequired[int]
    trigger_expr: NotRequired[str]
    plan_on_first_iteration: NotRequired[bool]


def _resident_behavior_edn(agent_id: str) -> str | None:
    """Return a personalized behavior EDN for a known resident, or None.

    Each resident gets a behavior tuned to their role — DSL dogfood:
    trigger expressions, stage variety, section ordering, ``:if``
    conditions, and reflect tuning on real agents.
    """
    residents: dict[str, _ResidentSpec] = {
        # ── 林知遥：开发工程师 ──
        # 深度分析型：先规划后执行，自我审查，不通过则修正
        "lin-zhiyao": {
            "stages": "think plan act critique revise",
            "max_iterations": 20,
            "sections": "persona protocol knowledge mail-history",
            "mail_history": 30,
        },
        # ── 江予白：运维工程师 ──
        # 谨慎简洁型：去掉 persona（系统操作不需要性格），快速决策
        "jiang-yubai": {
            "stages": "think act",
            "max_iterations": 5,
            "sections": "protocol knowledge mail-history",
            "mail_history": 10,
        },
        # ── 沈清晏：项目经理 ──
        # 协调规划型：仅首轮出计划（first-iteration? 条件），之后直接执行
        "shen-qingyan": {
            "stages": "think plan act",
            "max_iterations": 8,
            "sections": "persona protocol mail-history",
            "mail_history": 15,
            "plan_on_first_iteration": True,
        },
        # ── 顾言蹊：客服专员 ──
        # 快速分流型：极低迭代，表达式触发——点名或内容含"紧急"立即响应
        "gu-yanxi": {
            "stages": "think act",
            "max_iterations": 3,
            "sections": "persona protocol knowledge mail-history",
            "mail_history": 20,
            "trigger_expr": '(or (addressed-to-me? msg) (content-matches? msg "紧急"))',
        },
        # ── 苏晚晴：产品经理 ──
        # 需求审查型：执行后自我 critique，高频反思保持敏感
        "su-wanqing": {
            "stages": "think act critique",
            "max_iterations": 12,
            "sections": "persona protocol knowledge mail-history",
            "mail_history": 20,
            "reflect_minutes": 5,
        },
        # ── 谢云舒：数据分析师 ──
        # 方法驱动型：knowledge 前置（数据词典优先），plan→act→critique
        "xie-yunshu": {
            "stages": "think plan act critique",
            "max_iterations": 15,
            "sections": "knowledge persona protocol mail-history",
            "mail_history": 25,
            "reflect_minutes": 30,
        },
    }
    spec = residents.get(agent_id)
    if spec is None:
        return None
    comment = f"; 居民 {agent_id} — 个性化 behavior（阶段 7d 狗粮）\n"
    return comment + _mk_spec(**spec)


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


def _require(data: dict[str, Any], key: str, path: str) -> Any:
    """Read a required key or fail validation (total semantics)."""
    if key not in data:
        raise SpecValidationError(f"missing required {path}")
    return data[key]


def _read(data: dict[str, Any], key: str, path: str, kind: type) -> Any:
    """Read a required scalar field, validating its type."""
    value = _require(data, key, path)
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


def _require_section(data: dict[str, Any], key: str) -> dict[str, Any]:
    """Read a required subsection map, warning on unknown keys."""
    raw = data[key]  # presence guaranteed by from_dict / caller
    if not isinstance(raw, dict):
        kebab = key.replace("_", "-")
        raise SpecValidationError(
            f":{kebab} must be a map, got {_type_name(raw)}: {raw!r}"
        )
    _warn_unknown_keys(raw, key)
    return raw


def _warn_unknown_keys(raw: dict[str, Any], section: str) -> None:
    known = _KNOWN_KEYS[section]
    for key in raw:
        if key not in known:
            logger.warning("agent_spec_unknown_key", section=section, key=key)


_KNOWN_KEYS: dict[str, frozenset[str]] = {
    "top": frozenset({"version", "trigger", "context", "loop", "on_complete"}),
    # The retired proactive key stays "known" so old spec files are
    # silently ignored rather than warned about (proactive died with R1b).
    "trigger": frozenset({"on_mention", "proactive", "when"}),
    # Legacy boolean/count keys (persona/protocol/knowledge/mail-history)
    # are retired with the total-spec migration: they now warn as unknown
    # keys — :sections is required and is the only context input.
    "context": frozenset({"sections"}),
    "loop": frozenset({"max_iterations", "max_consecutive_errors", "stages", "until"}),
    "on_complete": frozenset({"save_interaction", "reflect", "actions"}),
    "reflect": frozenset({"enabled", "interval_minutes"}),
}


def _trigger_from(data: dict[str, Any]) -> TriggerSpec:
    return TriggerSpec(
        on_mention=_read(data, "on_mention", path=":trigger/:on-mention", kind=bool),
        when=_when_from(data.get("when")),
    )


def _when_from(value: Any) -> "Form | None":
    """Read an optional trigger expression from the dict path.

    Accepts a parsed Form, an EDN source string (``to_dict`` output), or
    None. Anything else is a validation error.
    """
    if value is None:
        return None
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


def _trigger_section(data: dict[str, Any]) -> TriggerSpec:
    """Build the trigger section: data map (declarative) or Form (expression).

    In expression mode the ``when`` expression takes precedence over the
    declarative rule entirely, so ``on_mention`` is pinned to True (the
    inbound-mail trigger is the expression alone).
    """
    raw = data["trigger"]  # presence guaranteed by from_dict
    if isinstance(raw, Form):
        return TriggerSpec(on_mention=True, when=_validate_trigger_expr(raw))
    if not isinstance(raw, dict):
        raise SpecValidationError(
            f":trigger must be a map or an expression, got {_type_name(raw)}: {raw!r}"
        )
    _warn_unknown_keys(raw, "trigger")
    return _trigger_from(raw)


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


def _context_section(data: dict[str, Any]) -> ContextSpec:
    """Build the context section: ``:sections`` vector, required."""
    raw = _require_section(data, "context")
    return ContextSpec(
        sections=_sections_from(_require(raw, "sections", ":context/:sections"))
    )


def _sections_from(value: Any) -> tuple[Form, ...]:
    """Read the ``:sections`` vector: each item must be a section form.

    Accepts parsed Forms (``from_edn`` path) or EDN source strings
    (``to_dict`` roundtrip); every form is validated against
    :data:`SECTION_NAMES` before it ever reaches an agent.
    """
    if isinstance(value, Form) or not isinstance(value, (list, tuple)):
        raise SpecValidationError(
            f":context/:sections must be a vector, got {_type_name(value)}: {value!r}"
        )
    forms: list[Form] = []
    seen: set[str] = set()
    for item in value:
        form = item
        if type(form) is str:
            # EDN source string (to_dict roundtrip path)
            try:
                form = loads(form)
            except EDNParseError as e:
                raise SpecValidationError(
                    f"invalid :context/:sections expression: {e}"
                ) from e
        valid = _validate_section_form(form, item)
        name = str(valid[0])
        if name in seen:
            raise SpecValidationError(f"duplicate section {name!r} in :context/:sections")
        seen.add(name)
        forms.append(valid)
    return tuple(forms)


def _validate_section_form(form: Any, original: Any) -> Form:
    """Validate one section form: ``(name)`` or ``(mail-history N)``, whitelisted."""
    if not isinstance(form, Form) or not form:
        raise SpecValidationError(
            f":context/:sections items must be section forms like (persona), "
            f"got {_type_name(original)}: {original!r}"
        )
    head = form[0]
    if not isinstance(head, Symbol):
        raise SpecValidationError(
            f":context/:sections form head must be a symbol, got {dumps(head)}"
        )
    name = str(head)
    if name not in SECTION_NAMES:
        raise SpecValidationError(
            f"unknown section {name!r} in :context/:sections (allowed: {sorted(SECTION_NAMES)})"
        )
    if name == "mail-history":
        if len(form) != 2:
            raise SpecValidationError(
                f"section 'mail-history' takes exactly one count argument, got {dumps(form)}"
            )
        count = form[1]
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise SpecValidationError(
                f"section 'mail-history' count must be a non-negative int, got {dumps(count)}"
            )
    elif len(form) != 1:
        raise SpecValidationError(f"section {name!r} takes no arguments, got {dumps(form)}")
    return form


def _loop_section(data: dict[str, Any]) -> LoopSpec:
    raw = _require_section(data, "loop")
    return LoopSpec(
        max_iterations=_read(raw, "max_iterations", path=":loop/:max-iterations", kind=int),
        max_consecutive_errors=_read(
            raw, "max_consecutive_errors", path=":loop/:max-consecutive-errors", kind=int
        ),
        stages=_stages_from(_require(raw, "stages", ":loop/:stages")),
        until=_until_from(_require(raw, "until", ":loop/:until")),
    )


def _stages_from(value: Any) -> tuple[Form, ...]:
    """Read the ``:stages`` vector: each item must be a stage form.

    Accepts parsed Forms (``from_edn`` path) or EDN source strings
    (``to_dict`` roundtrip); every form is validated against
    :data:`STAGE_NAMES` before it ever reaches an agent.
    """
    if isinstance(value, Form) or not isinstance(value, (list, tuple)):
        raise SpecValidationError(
            f":loop/:stages must be a vector, got {_type_name(value)}: {value!r}"
        )
    if not value:
        raise SpecValidationError(
            ":loop/:stages must not be empty (no stage would ever advance the loop)"
        )
    forms: list[Form] = []
    for item in value:
        form = item
        if type(form) is str:
            # EDN source string (to_dict roundtrip path)
            try:
                form = loads(form)
            except EDNParseError as e:
                raise SpecValidationError(
                    f"invalid :loop/:stages expression: {e}"
                ) from e
        forms.append(_validate_stage_form(form, item))
    return tuple(forms)


def _validate_stage_form(form: Any, original: Any) -> Form:
    """Validate one stage form: ``(name)`` or ``(name {opts})``, name whitelisted."""
    if not isinstance(form, Form) or not form:
        raise SpecValidationError(
            f":loop/:stages items must be stage forms like (think), "
            f"got {_type_name(original)}: {original!r}"
        )
    head = form[0]
    if not isinstance(head, Symbol):
        raise SpecValidationError(
            f":loop/:stages form head must be a symbol, got {dumps(head)}"
        )
    name = str(head)
    if name not in STAGE_NAMES:
        raise SpecValidationError(
            f"unknown stage {name!r} in :loop/:stages (allowed: {sorted(STAGE_NAMES)})"
        )
    if len(form) > 2:
        raise SpecValidationError(
            f"stage {name!r} takes at most one opts map, got {dumps(form)}"
        )
    if len(form) == 2:
        opts = form[1]
        if isinstance(opts, Form) or not isinstance(opts, dict):
            raise SpecValidationError(
                f"stage {name!r} opts must be a map, got {dumps(opts)}"
            )
        _validate_stage_opts(name, opts, form)
    return form


def _validate_stage_opts(name: str, opts: dict[Any, Any], form: Form) -> None:
    """Validate one stage's opts map (phase 7c).

    Every stage accepts an optional ``:if`` condition expression,
    statically validated against :data:`STAGE_COND_BUILTINS` with an
    empty scope (loop state reaches builtins through env, same pattern
    as the until expression). ``plan`` additionally accepts
    ``:max-steps`` (positive int; the runtime default is 5). Unknown
    keys are rejected.
    """
    allowed = {"if", "max-steps"} if name == "plan" else {"if"}
    for key, value in opts.items():
        if not isinstance(key, Keyword):
            raise SpecValidationError(
                f"stage {name!r} opts keys must be keywords, got {_type_name(key)}: {key!r}"
            )
        if str(key) not in allowed:
            raise SpecValidationError(
                f"stage {name!r}: unknown opts key :{key} in {dumps(form)}"
            )
        if str(key) == "if":
            try:
                validate_form(value, STAGE_COND_BUILTINS, scope=set())
            except EvalError as e:
                raise SpecValidationError(f"stage {name!r} :if: {e}") from e
        elif not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise SpecValidationError(
                f"stage 'plan' :max-steps must be a positive int, got {dumps(value)}"
            )


def _until_from(value: Any) -> Form:
    """Read the loop termination expression from the dict path.

    Accepts a parsed Form or an EDN source string (``to_dict`` output),
    mirroring ``:trigger/:when``. Anything else is a validation error.
    """
    if isinstance(value, Form):
        return _validate_until_expr(value)
    if isinstance(value, str):
        try:
            parsed = loads(value)
        except EDNParseError as e:
            raise SpecValidationError(f"invalid :loop/:until expression: {e}") from e
        if not isinstance(parsed, Form):
            raise SpecValidationError(
                f":loop/:until must be an expression, got {_type_name(parsed)}: {parsed!r}"
            )
        return _validate_until_expr(parsed)
    raise SpecValidationError(
        f":loop/:until must be an expression, got {_type_name(value)}: {value!r}"
    )


def _validate_until_expr(form: Form) -> Form:
    """Statically validate an until expression at load time (no execution).

    Only whitelisted special forms and the pure ``LOOP_BUILTINS``
    predicates are accepted; the expression takes no symbols (loop state
    reaches builtins through ``env``, same pattern as
    :func:`_validate_trigger_expr`).
    """
    try:
        validate_form(form, LOOP_BUILTINS, scope=set())
    except EvalError as e:
        raise SpecValidationError(f"invalid :loop/:until expression: {e}") from e
    return form


def _on_complete_section(data: dict[str, Any]) -> OnCompleteSpec:
    raw = _require_section(data, "on_complete")
    reflect_raw = _require(raw, "reflect", ":on-complete/:reflect")
    if not isinstance(reflect_raw, dict):
        raise SpecValidationError(
            f":on-complete/:reflect must be a map, "
            f"got {_type_name(reflect_raw)}: {reflect_raw!r}"
        )
    _warn_unknown_keys(reflect_raw, "reflect")
    return OnCompleteSpec(
        save_interaction=_read(
            raw, "save_interaction", path=":on-complete/:save-interaction", kind=bool
        ),
        reflect=_reflect_from(reflect_raw),
        actions=_actions_from(raw.get("actions")),
    )


def _actions_from(value: Any) -> tuple[Form, ...]:
    """Read the optional ``:actions`` vector: each item must be an action form.

    Accepts parsed Forms (``from_edn`` path) or EDN source strings
    (``to_dict`` roundtrip); every form is statically validated against
    the two-position purity rules (pure ``when`` condition, action
    positions) before it ever reaches an agent.
    """
    if value is None:
        return ()
    if isinstance(value, Form) or not isinstance(value, (list, tuple)):
        raise SpecValidationError(
            f":on-complete/:actions must be a vector, got {_type_name(value)}: {value!r}"
        )
    forms: list[Form] = []
    for item in value:
        form = item
        if type(form) is str:
            # EDN source string (to_dict roundtrip path)
            try:
                form = loads(form)
            except EDNParseError as e:
                raise SpecValidationError(
                    f"invalid :on-complete/:actions expression: {e}"
                ) from e
        if not isinstance(form, Form):
            raise SpecValidationError(
                f":on-complete/:actions items must be expressions, "
                f"got {_type_name(item)}: {item!r}"
            )
        try:
            validate_action_form(form, scope={"msg", "result", "self"})
        except EvalError as e:
            raise SpecValidationError(f"invalid :on-complete/:actions expression: {e}") from e
        forms.append(form)
    return tuple(forms)


def _reflect_from(data: dict[str, Any]) -> ReflectSpec:
    return ReflectSpec(
        enabled=_read(data, "enabled", path=":on-complete/:reflect/:enabled", kind=bool),
        interval_minutes=_read(
            data,
            "interval_minutes",
            path=":on-complete/:reflect/:interval-minutes",
            kind=int,
        ),
    )
