"""Adam — the built-in init process of the agent world (PID 1).

Policy/mechanism split (docs/design/members.md, 阶段 D): the AgentRegistry
is pure mechanism (assembly, run table, locks, budget, idle-fact
detection); Adam is the policy. The policy is *data*, not code: Adam is
the first configuration instance of the unified MemberRuntime
(``agents/member_runtime.py``, phase C) — a built-in MemberSpec (the
``ADAM_SPEC`` text below) parsed directly with the unified loader
semantics and validated at load, plus registry query closures injected
into the evaluator. Five policy lines live in the spec:

1. boot — after initialize(), every registered member whose spec
   declares ``:on :boot`` (phase E: spec-derived, see ``boot_candidates``)
   that is not running gets an AGENT_START_REQUEST{reason="boot"}. The
   ``member-start-all`` operation expands the candidate set in Python — the DSL
   never executes iteration, iteration is a builtin.
2. drain — the registry's AGENT_IDLE fact event is turned into an
   AGENT_STOP_REQUEST{reason="drain"}.
3. welcome (birth socialization, ADR 0003) — AGENT_SPAWNED triggers a
   welcome mail to the newborn member: a greeting naming the new member,
   pointers to its own discovery surfaces (its contact book and the
   member-list addressing yellow page) and the static communication
   conventions. The recipient address
   is assembled in the spec with ``str`` — mail-send recipients are mail
   addresses (``Mail.create`` rejects bare ids). Adam is v1's mentor:
   mentoring is spec-described behavior, not a dedicated mechanism.
4. guardian-inbox (docs/design/mail.md 「守护者信箱」) — MAIL_DELIVERED
   addressed to Adam gets a
   fixed acknowledgement reply: the guardian's mailbox is a message box,
   and the enforcer of the guardian rules is currently the human
   (staging proposals are approved by humans on the admin surface;
   retirement/stop requests go through the management termination flow). Mail from
   postmaster is excluded (``from?``) so a bounce never gets answered.
   Adam carries a real mailbox (``~/.tlon/adam/mail/``), so read-marking
   and the boot unread replay come for free.
5. noisy-circuit-breaker — the PublicHealthMonitor's AGENT_NOISY fact
   (a sender crossing the mail-rate threshold) is turned
   into an AGENT_STOP_REQUEST: a member flooding the society is put to
   sleep (never deleted — wake-on-mail can revive it). The monitor's
   per-sender cooldown keeps repeated facts from repeated stops; the
   registry executes stop requests idempotently. MAIL_ORPHAN_SCAN, the
   monitor's other fact, is deliberately *not* consumed: it is periodic,
   and any stateless reaction to a periodic fact would itself become
   periodic noise — a correct consumer needs hysteresis state or a human
   digest channel, neither of which the DSL offers today.

Wake is no longer Adam's line: phase E moved wake-on-mail into the
registry as a mechanized policy — the registry derives wake eligibility
from each member's spec (``:on :mail-delivered``) and issues its own
AGENT_START_REQUEST{reason="wake"}, so the spec is the single lifecycle
authority for both the boot set and the wake gate.

Adam is a built-in Member (``member_id="adam"``; its spec has no loop
rule): it does not come from
any user spec file. It stays outside the registry's activation table and
is discoverable through the public member directory. Adam holds the same
registry as ordinary runtimes: ``LifecycleService`` uses its boot candidates
and publishes start/stop requests, while ``RegistryQueries`` reads registry
state without reconstructing it from events. Lifecycle decisions carry no dedup
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
from tlon.organization.mail.post_office import PostOffice
from tlosure.edn import loads

#: Adam's policy, as data. Parsed once per assembly by
#: :func:`make_adam`. Five rules: boot (member-start-all), drain (idle → stop),
#: welcome (ADR 0003 — a birth-socialization mail to every spawned
#: member; it points the newborn at its system prompt's team section and
#: the member-list tool for discovering peers, the conventions are static
#: text), guardian-inbox (docs/design/mail.md 「守护者信箱」 — a fixed
#: acknowledgement reply to mail
#: addressed to Adam; postmaster senders are excluded so a bounce never
#: gets an answer) and noisy-circuit-breaker (AGENT_NOISY → stop the
#: flooding member; sleep, never delete).
ADAM_SPEC_EDN = r"""
{:version 1
 :rules
 [{:name "boot" :on :boot
   :actions [(member-start-all {:reason "boot"})]}
  {:name "drain" :on :agent-idle
   :actions [(member-stop {:agent_id (get event :agent_id) :reason "drain"})]}
  {:name "noisy-circuit-breaker" :on :agent-noisy
   :actions [(member-stop {:agent_id (get event :agent_id) :reason "noisy-circuit-breaker"})]}
  {:name "welcome" :on :agent-spawned
   :actions
   [(mail-send
      {:to [(str (get event :agent_id) "@tlon.internal")]
       :content
       (str "亲爱的 " (get event :name) "：\n\n"
            "欢迎出生。我是 Adam，这个世界的守护者。\n\n"
            "认识其他成员：通信即结识——把对一个人的认识记进你自己的联系簿"
            "（contacts-upsert：名字 / 地址 / 我对 ta 的描述），"
            "它会注入你的系统提示词；"
            "查谁的地址用 member-list 成员寻址黄页（公开 id / 名字 / 地址），黄页可以引用和转告，不认证职务，也不公开私人材料。\n\n"
            "通信惯例：\n"
            "- 成员之间以邮件往来，收件人写对方的名字或地址（名字@tlon.internal）。\n"
            "- 有事找守护者，写信到 adam@tlon.internal。\n")})]}
  {:name "guardian-inbox" :on :mail-delivered
   :when (and (addressed-to-me? msg) (not (from? msg "postmaster@tlon.internal")))
   :actions
   [(mail-send
      {:to [(mail-field msg :from)]
       :in_reply_to (mail-field msg :id)
       :content "已收到你的来信。\n\n我是 Adam，这个世界的守护者。守护规则的执行者当前是人类：staging 原型提案由人类在管理面审批，成员只通过 exit-agent 管理自己的退出；终止其他成员由人类管理端处理。\n\n本信箱是留言箱——这封回信仅确认收讫。"})
    (mail-mark-read {:mail_ids [(mail-field msg :id)]})]}]}
"""


def make_adam(
    event_bus: EventBus,
    agent_registry: AgentRegistry,
    tlon_paths: TlonPaths,
    post_office: PostOffice | None = None,
) -> MemberRuntime:
    """Assemble Adam: the built-in spec plus registry query closures.

    Adam receives the registry for shared lifecycle operations and query
    closures. It remains outside the registry's activation table; the
    dependency stays one-directional: Adam → registry, never reverse.

    Args:
        event_bus: Event bus for subscriptions and request publishing
        agent_registry: Shared lifecycle and query services; start/stop decisions
            still publish requests through the event bus
        tlon_paths: Path resolver. Adam's mailbox lives at
            ``~/.tlon/adam/mail/`` (docs/design/mail.md 「守护者信箱」) —
            read-marking and the boot unread replay come from it.
    """
    builtins = make_member_builtins(RegistryQueries(agent_registry))
    spec = MemberSpec.from_edn(loads(ADAM_SPEC_EDN))
    # Query closures and shared Operation names use the same load-time gate.
    spec = validate_member_spec(spec, builtins=builtins)
    return MemberRuntime(
        member_id="adam",
        name="adam",
        event_bus=event_bus,
        spec=spec,
        builtins=builtins,
        mailbox=Mailbox(tlon_paths.mail_database_file(), "adam"),
        capabilities=None,
        registry=agent_registry,
        tlon_paths=tlon_paths,
        post_office=post_office,
    )
