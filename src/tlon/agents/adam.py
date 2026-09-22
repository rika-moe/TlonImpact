"""Adam — the built-in init process of the agent world (PID 1).

Policy/mechanism split (docs/design/members.md, 阶段 D): the AgentRegistry
is pure mechanism (assembly, run table, locks, budget, idle-fact
detection); Adam is the policy. The policy is *data*, not code: Adam is
the first configuration instance of the unified MemberRuntime
(``agents/member_runtime.py``, phase C) — a built-in MemberSpec (the
``ADAM_SPEC`` text below) parsed directly with the unified loader
semantics and validated at load, plus registry query closures injected
into the evaluator. Four policy lines live in the spec:

1. boot — after initialize(), every registered member whose spec
   declares ``:on :boot`` (phase E: spec-derived, see ``boot_candidates``)
   that is not running gets an AGENT_START_REQUEST{reason="boot"}. The
   ``start-all`` action expands the candidate set in Python — the DSL
   never executes iteration, iteration is a builtin.
2. drain — the registry's AGENT_IDLE fact event is turned into an
   AGENT_STOP_REQUEST{reason="drain"}.
3. welcome (issue , birth socialization) — AGENT_SPAWNED triggers a
   welcome mail to the newborn member: a greeting naming the new member,
   the social roster (``member-roster``, dynamically expanded by the
   host) and the static communication conventions. The recipient address
   is assembled in the spec with ``str`` — emit recipients are mail
   addresses (``Mail.create`` rejects bare ids). Adam is v1's mentor:
   mentoring is spec-described behavior, not a dedicated mechanism.
4. guardian-inbox (issue ) — MAIL_DELIVERED addressed to Adam gets a
   fixed acknowledgement reply: the guardian's mailbox is a message box,
   and the enforcer of the guardian rules is currently the human
   (staging proposals are approved by humans on the admin surface;
   retirement/stop requests go through the stopAgent flow). Mail from
   postmaster is excluded (``from?``) so a bounce never gets answered.
   Adam carries a real mailbox (``~/.tlon/adam/mail/``), so read-marking
   and the boot unread replay come for free.

Wake is no longer Adam's line: phase E moved wake-on-mail into the
registry as a mechanized policy — the registry derives wake eligibility
from each member's spec (``:on :mail-delivered``) and issues its own
AGENT_START_REQUEST{reason="wake"}, so the spec is the single lifecycle
authority for both the boot set and the wake gate.

Adam is a built-in Member (``member_id="adam"``; its spec has no loop
rule): it does not come from
any user spec file, is not registered in the agent registry, and does
not appear in MemberDirectory. It holds no run-state of its own — the
injected query closures (the shared ``RegistryQueries`` implementation,
imported from the registry module) read the registry's read-only
interfaces (``get_agent`` / ``get_config`` / ``get_all``) instead of
reconstructing state from events. Lifecycle decisions carry no dedup
state either: the registry executes requests idempotently.

The dependency is one-directional: Adam imports the registry; the
registry never imports Adam (kernel first, PID 1 second — the DAG stays
acyclic).
"""

from tlon.agents.agent_registry import AgentRegistry, RegistryQueries
from tlon.agents.member_runtime import MemberRuntime
from tlon.agents.member_spec import MemberSpec, validate_member_spec
from tlon.agents.vocabulary import make_member_builtins
from tlon.base.events.event_bus import EventBus
from tlon.base.paths import TlonPaths
from tlon.organization.mail.mailbox import Mailbox
from tlosure.edn import loads

#: Adam's policy, as data. Parsed once per assembly by
#: :func:`make_adam`. Four rules: boot (start-all), drain (idle → stop),
#: welcome (issue  — a birth-socialization mail to every spawned
#: member; the roster is host-expanded, the conventions are static text)
#: and guardian-inbox (issue  — a fixed acknowledgement reply to mail
#: addressed to Adam; postmaster senders are excluded so a bounce never
#: gets an answer).
ADAM_SPEC_EDN = r"""{:version 1
 :rules [{:name "boot"
          :on :boot
          :actions [(start-all {:reason "boot"})]}
         {:name "drain"
          :on :agent-idle
          :actions [(stop {:agent (get event :agent_id) :reason "drain"})]}
         {:name "welcome"
          :on :agent-spawned
          :actions [(emit (str "亲爱的 " (get event :name) "：\n\n"
                               "欢迎出生。我是 Adam，这个世界的守护者。\n\n"
                               "这个社会的成员：\n" (member-roster) "\n\n"
                               "通信惯例：\n"
                               "- 成员之间以邮件往来，收件人写对方的名字或地址（名字@tlon.internal）。\n"
                               "- 要广播给全体成员，写给 all@tlon.internal。\n"
                               "- 有事找守护者，写信到 adam@tlon.internal。\n")
                          [(str (get event :agent_id) "@tlon.internal")])]}
         {:name "guardian-inbox"
          :on :mail-delivered
          :when (and (addressed-to-me? msg)
                     (not (from? msg "postmaster@tlon.internal")))
          :actions [(emit "已收到你的来信。\n\n我是 Adam，这个世界的守护者。守护规则的执行者当前是人类：staging 原型提案由人类在管理面审批，成员的退休/停止请求走 stopAgent 流程。\n\n本信箱是留言箱——这封回信仅确认收讫。")]}]}
"""


def make_adam(
    event_bus: EventBus,
    agent_registry: AgentRegistry,
    tlon_paths: TlonPaths,
) -> MemberRuntime:
    """Assemble Adam: the built-in spec plus registry query closures.

    Adam is a deterministic member (no cognitive rule → cognitive=None): it
    holds no registry reference beyond the injected query closures — the
    shared ``RegistryQueries`` implementation imported from the registry
    module (one-directional dependency: Adam → registry, never reverse).

    Args:
        event_bus: Event bus for subscriptions and request publishing
        agent_registry: The registry to query (read-only interfaces) and
            to address with start/stop requests
        tlon_paths: Path resolver. Adam's mailbox lives at
            ``~/.tlon/adam/mail/`` (issue  guardian inbox) —
            read-marking and the boot unread replay come from it.
    """
    builtins = make_member_builtins(RegistryQueries(agent_registry))
    spec = MemberSpec.from_edn(loads(ADAM_SPEC_EDN))
    # Single-validator gate with the host builtins: the boot rule's
    # ``start-all`` action needs the registry host for candidate expansion,
    # which the host-less deterministic branch would reject.
    spec = validate_member_spec(spec, builtins=builtins)
    return MemberRuntime(
        member_id="adam",
        name="adam",
        event_bus=event_bus,
        spec=spec,
        builtins=builtins,
        mailbox=Mailbox(tlon_paths.member_mail_dir("adam")),
        cognitive=None,
    )
