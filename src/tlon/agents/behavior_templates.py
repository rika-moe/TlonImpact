"""Rules-form behavior templates (write side, phase C).

The wizard's default behavior and the stage-7d resident dogfood
programs, emitted in the unified rules form (``{:version 1 :rules [...]}``)
that every member's ``behavior.tlon`` now carries. Relocated from the
legacy ``agents/spec.py`` — the old four-section AgentSpec shape
(``:trigger`` / ``:context`` / ``:loop`` / ``:on-complete``) is gone.

The standard program is the canonical ReAct loop; residents get
personalized programs tuned to their roles (stage 7d dogfood: trigger
expressions, stage variety, section ordering, ``:if`` conditions, reflect
tuning). These are building blocks for authoring behavior files, never
implicit defaults — every file must spell its program out in full.
"""

from typing import Any, NotRequired, TypedDict

from tlon.agents.edn import Keyword, dumps, loads
from tlon.agents.member_spec import MEMBER_SPEC_VERSION

#: The standard ReAct program: the canonical stage vector and until
#: expression, used by the wizard template and tests. These are building
#: blocks for authoring a spec file, never implicit defaults.
_REACT_STAGES: tuple[str, ...] = ("(think)", "(act)")
_REACT_UNTIL: str = "(or (iteration-limit-reached?) (error-limit-reached?))"

#: Default trigger predicate: respond when my address is in To or CC.
_TRIGGER_PREDICATE = "(or (addressed-to-me? msg) (cc-ed? msg))"

#: Default context program (phase 7b): persona + protocol in the system
#: prompt, then knowledge, then 20 mails of history in the conversation.
_DEFAULT_SECTIONS: tuple[str, ...] = (
    "(persona)",
    "(protocol)",
    "(knowledge)",
    "(mail-history 20)",
)


def standard_behavior_edn(agent_id: str | None = None) -> str:
    """The canonical complete behavior file (standard ReAct program).

    Used by the wizard when creating an agent: the file spells out every
    rule in full (:func:`MemberSpec.from_edn` accepts it), built from
    :data:`_REACT_STAGES` / :data:`_REACT_UNTIL` /
    :data:`_DEFAULT_SECTIONS`.

    When ``agent_id`` matches a known resident, a personalized behavior is
    returned instead — the stage 7d dogfood path: exercise DSL features
    (trigger expressions, stage variety, section ordering, ``:if``
    conditions, reflect tuning) on real agents.
    """
    if agent_id is not None:
        resident = _resident_behavior_edn(agent_id)
        if resident is not None:
            return resident
    return _mk_spec(
        stages="think act",
        max_iterations=10,
        sections="persona protocol knowledge mail-history",
        mail_history=20,
    )


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
    extra_rules: tuple[dict[str, Any], ...] = (),
) -> str:
    """Build a rules-form behavior EDN string from a compact specification.

    ``stages`` is a space-separated list of stage keywords (e.g.
    ``"think plan act critique revise"``). ``sections`` is a
    space-separated ordered list of section keywords (e.g.
    ``"persona protocol knowledge"``). ``trigger_expr``, when set, is a
    raw EDN predicate expression placed in the rule's ``:when`` slot;
    otherwise the default ``(or (addressed-to-me? msg) (cc-ed? msg))``
    applies. ``plan_on_first_iteration`` gates the ``plan`` stage behind
    ``:if (first-iteration?)`` (plan once, then execute — dogfood for
    the ``first-iteration?`` stage condition).

    ``extra_rules`` (phase F): deterministic ``:on :mail-delivered`` rules
    prepended before ``respond`` — the cheap path of a mixed member
    (cost routing / deflection). Each entry is ``{"name", "when",
    "actions"}`` of raw EDN strings; when set, ``trigger_expr`` should
    ``(not ...)``-exclude whatever they handle.
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

    stage_forms = [loads(stage_map[s]) for s in stages.split()]
    section_forms = [loads(section_map[s]) for s in sections.split()]
    when = loads(trigger_expr) if trigger_expr is not None else loads(_TRIGGER_PREDICATE)
    until = loads(_REACT_UNTIL)

    deterministic: list[dict[Any, Any]] = [
        {
            Keyword("name"): extra["name"],
            Keyword("on"): Keyword("mail-delivered"),
            Keyword("when"): loads(extra["when"]),
            Keyword("actions"): [loads(a) for a in extra["actions"]],
        }
        for extra in extra_rules
    ]

    data: dict[Any, Any] = {
        Keyword("version"): MEMBER_SPEC_VERSION,
        Keyword("rules"): [
            *deterministic,
            {
                Keyword("name"): "respond",
                Keyword("on"): Keyword("mail-delivered"),
                Keyword("actions"): [],
                Keyword("when"): when,
                Keyword("loop"): {
                    Keyword("context"): {Keyword("sections"): section_forms},
                    Keyword("stages"): stage_forms,
                    Keyword("until"): until,
                    Keyword("max-iterations"): max_iterations,
                    Keyword("max-consecutive-errors"): max_consecutive_errors,
                    Keyword("save-interaction"): True,
                    Keyword("reflect"): {
                        Keyword("enabled"): True,
                        Keyword("interval-minutes"): reflect_minutes,
                    },
                },
            },
            # Boot rule (phase E): declares startup — this member belongs to
            # the boot set. Empty actions: boot-time has no mail in context,
            # so the rule can only carry lifecycle intent. Appended after
            # "respond" so rule[0] stays the mail rule.
            {
                Keyword("name"): "boot",
                Keyword("on"): Keyword("boot"),
                Keyword("actions"): [],
            },
        ],
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
    extra_rules: NotRequired[tuple[dict[str, str | list[str]], ...]]


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
        # 快速分流型：极低迭代，表达式触发——点名或内容含"紧急"立即响应；
        # 阶段 F 混排狗粮：确定性 auto-optout 拦截含"退订"的邮件（零 LLM），
        # respond 用 :when 排除便宜路径已处理的（deflection / cost routing）
        "gu-yanxi": {
            "stages": "think act",
            "max_iterations": 3,
            "sections": "persona protocol knowledge mail-history",
            "mail_history": 20,
            "trigger_expr": (
                '(and (or (addressed-to-me? msg) (content-matches? msg "紧急"))'
                ' (not (content-matches? msg "退订")))'
            ),
            "extra_rules": (
                {
                    "name": "auto-optout",
                    "when": '(content-matches? msg "退订")',
                    "actions": ['(emit "已为您处理退订" [])'],
                },
            ),
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
