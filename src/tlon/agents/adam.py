"""Adam's five lifecycle policies are ordinary module event handlers.

The registry owns lifecycle mechanics and wake eligibility. Adam is the
policy member outside its activation table: boot, drain, noisy circuit
breaker, newborn welcome and guardian mailbox acknowledgement.
"""

from tlon.agents.agent_registry import AgentRegistry
from tlon.agents.member_runtime import MemberRuntime
from tlon.agents.program_package import validate_program_sources
from tlon.agents.vocabulary import make_member_builtins
from tlon.base.events.event_bus import EventBus
from tlon.base.paths import TlonPaths
from tlon.organization.mail.mailbox import Mailbox
from tlon.organization.mail.post_office import PostOffice

ADAM_SPEC_EDN = r"""
{:version 2 :handlers {:boot adam.policy/boot :agent-idle adam.policy/drain :agent-noisy adam.policy/noisy_circuit_breaker :agent-spawned adam.policy/welcome :mail-delivered adam.policy/guardian_inbox} :fuel 10000}
"""

ADAM_MODULE = r"""
(ns adam.policy (:export boot drain noisy_circuit_breaker welcome guardian_inbox))
(defn boot [event] (let [out0 (member-start-all {:reason "boot"})] {:result nil}))
(defn drain [event] (let [out0 (member-stop {:agent_id (get (get event :payload) :agent_id) :reason "drain"})] {:result nil}))
(defn noisy_circuit_breaker [event] (let [out0 (member-stop {:agent_id (get (get event :payload) :agent_id) :reason "noisy-circuit-breaker"})] {:result nil}))
(defn welcome [event] (let [out0 (mail-send {:to [(str (get (get event :payload) :agent_id) "@tlon.internal")] :content (str "亲爱的 " (get (get event :payload) :name) "：\n\n" "欢迎出生。我是 Adam，这个世界的守护者。\n\n" "认识其他成员：通信即结识——把对一个人的认识记进你自己的联系簿" "（contacts-upsert：名字 / 地址 / 我对 ta 的描述），" "它会注入你的系统提示词；" "查谁的地址用 member-list 成员寻址黄页（公开 id / 名字 / 地址），黄页可以引用和转告，不认证职务，也不公开私人材料。\n\n" "通信惯例：\n" "- 成员之间以邮件往来，收件人写对方的名字或地址（名字@tlon.internal）。\n" "- 有事找守护者，写信到 adam@tlon.internal。\n")})] {:result nil}))
(defn guardian_inbox [event] (if (and (contains? (or (mail-field (get event :mail) :to) []) (get (get event :self) :address)) (not (from? (get event :mail) "postmaster@tlon.internal"))) (let [out0 (mail-send {:to [(mail-field (get event :mail) :from)] :in_reply_to (mail-field (get event :mail) :id) :content "已收到你的来信。\n\n我是 Adam，这个世界的守护者。守护规则的执行者当前是人类：staging 原型提案由人类在管理面审批，成员通过工作程序的调度结果管理自己的退出；终止其他成员由人类管理端处理。\n\n本信箱是留言箱——这封回信仅确认收讫。"}) out1 (mail-mark-read {:mail_ids [(mail-field (get event :mail) :id)]})] {:result nil}) {:result nil}))

"""


def make_adam(
    event_bus: EventBus,
    agent_registry: AgentRegistry,
    tlon_paths: TlonPaths,
    post_office: PostOffice | None = None,
) -> MemberRuntime:
    """Assemble Adam: ordinary handlers with registry-bound operations.

    Adam receives the registry for shared lifecycle and read operations. It remains outside the registry's activation table; the
    dependency stays one-directional: Adam → registry, never reverse.

    Args:
        event_bus: Event bus for subscriptions and request publishing
        agent_registry: Shared lifecycle and query services; start/stop decisions
            still publish requests through the event bus
        tlon_paths: Path resolver. Adam's mailbox lives at
            ``~/.tlon/adam/mail/`` (docs/design/mail.md 「守护者信箱」) —
            read-marking and the boot unread replay come from it.
    """
    builtins = make_member_builtins()
    spec = validate_program_sources(
        ADAM_SPEC_EDN, {"adam/policy.tlon": ADAM_MODULE}, builtins=builtins
    )
    return MemberRuntime(
        member_id="adam",
        name="adam",
        event_bus=event_bus,
        spec=spec,
        builtins=builtins,
        mailbox=Mailbox(tlon_paths.mail_database_file(), "adam"),
        services=None,
        registry=agent_registry,
        tlon_paths=tlon_paths,
        post_office=post_office,
    )
