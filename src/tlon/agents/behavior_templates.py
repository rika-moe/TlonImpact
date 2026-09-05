"""Rules-form behavior template (write side, phase C).

The wizard's default behavior, emitted in the unified rules form
(``{:version 1 :rules [...]}``) that every member's ``behavior.tlon``
now carries.

Unified member model (ADR 0006): there are no resident personas
hardcoded in Python anymore — the per-resident dogfood programs
(``_RESIDENT_IDS`` etc.) are gone. Every member gets the canonical
standard program from here; personalized behavior comes from the
prototype library (data), never from code.

This is a building block for authoring behavior files, never an
implicit default — every file must spell its program out in full.
"""

from typing import Any

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
#: prompt, then knowledge, then the skills index (ADR 0010), then the
#: current doing task (issue ), then 20 mails of history in the
#: conversation.
_DEFAULT_SECTIONS: tuple[str, ...] = (
    "(persona)",
    "(protocol)",
    "(knowledge)",
    "(skills)",
    "(current-task)",
    "(mail-history 20)",
)


def standard_behavior_edn() -> str:
    """The canonical complete behavior file (standard ReAct program).

    Used by the wizard when creating an agent: the file spells out every
    rule in full (:func:`MemberSpec.from_edn` accepts it), built from
    :data:`_REACT_STAGES` / :data:`_REACT_UNTIL` /
    :data:`_DEFAULT_SECTIONS`.
    """
    data: dict[Any, Any] = {
        Keyword("version"): MEMBER_SPEC_VERSION,
        Keyword("rules"): [
            {
                Keyword("name"): "respond",
                Keyword("on"): Keyword("mail-delivered"),
                Keyword("actions"): [],
                Keyword("when"): loads(_TRIGGER_PREDICATE),
                Keyword("loop"): {
                    Keyword("context"): {
                        Keyword("sections"): [loads(s) for s in _DEFAULT_SECTIONS]
                    },
                    Keyword("stages"): [loads(s) for s in _REACT_STAGES],
                    Keyword("until"): loads(_REACT_UNTIL),
                    Keyword("max-iterations"): 10,
                    Keyword("max-consecutive-errors"): 3,
                    Keyword("reflect"): {
                        Keyword("enabled"): True,
                        Keyword("interval-minutes"): 15,
                    },
                },
            },
            # Boot rule (phase E): declares startup — this member belongs to
            # the boot set. Empty actions: boot-time has no mail in context,
            # so the rule can only carry lifecycle intent.
            {
                Keyword("name"): "boot",
                Keyword("on"): Keyword("boot"),
                Keyword("actions"): [],
            },
        ],
    }
    return dumps(data) + "\n"
