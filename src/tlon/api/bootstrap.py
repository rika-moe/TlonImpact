"""Runtime 装配（ADR 0019）：``build_runtime()`` 收编原
``ServerApp.initialize()`` 的 runtime 装配段——首启种子、EventBus、
ConfigService、MetricsService、TraceStore、AgentRegistry、
LessonsDistiller、RetirementCeremony、Adam、guardian 包装、外部网关
注册表、AdapterManager、MailDelivery、可选的 GitHubAdapter——并在其上
构造六个 domain facade。

``build_runtime(runtime_paths)`` 持有一个显式的 ``TlonPaths``：同一进程
可以并存多个 runtime 实例（各有独立的 tlon home）。进程级的
tracing/metrics 支柱（OTel provider 是进程级单例）不在此处初始化——
那是进程编排层（``tlon_server.serverapp.ServerApp``）的职责。

返回的 ``Runtime`` 是一个小 container：持有各 facade（server 的注入
面）与 runtime 组件（shutdown 用）。server 只装配自己的
session/notification/HTTP 生命周期。
"""

from dataclasses import dataclass

from tlon.agents.adam import make_adam
from tlon.agents.agent_registry import AgentRegistry
from tlon.agents.bootstrap import bootstrap_society
from tlon.agents.lessons import LessonsDistiller
from tlon.agents.member_runtime import MemberRuntime
from tlon.agents.retirement import RetirementCeremony
from tlon.api.config import ConfigFacade
from tlon.api.events import EventsFacade
from tlon.api.mail import MailFacade
from tlon.api.members import MembersFacade
from tlon.api.observability import ObservabilityFacade
from tlon.api.prototypes import PrototypesFacade
from tlon.base.config import ConfigService
from tlon.base.events.event_bus import EventBus
from tlon.base.logger import get_logger
from tlon.base.metrics import MetricsService
from tlon.base.paths import TlonPaths
from tlon.base.settings import get_settings
from tlon.base.tracing.trace_store import FileTraceStore
from tlon.organization.mail.delivery import MailDelivery
from tlon.organization.mail.gateways import ExternalGatewayRegistry
from tlon.organization.mail.guardian_provider import GuardianAgentInfoProvider
from tlon.organization.messaging.github_adapter import GitHubAdapter
from tlon.organization.messaging.human_adapter import AdapterManager

logger = get_logger(__name__)


@dataclass
class Runtime:
    """``build_runtime()`` 的装配结果：runtime 组件 + 六个 domain facade。

    server（tlon_server）只许经 facade 字段触达 runtime；组件字段
    留给生命周期管理（``shutdown``）与事件发布（``event_bus``）。
    """

    # runtime 组件（shutdown 顺序见 Runtime.shutdown）
    event_bus: EventBus
    config_service: ConfigService
    metrics_service: MetricsService
    trace_store: FileTraceStore
    agent_registry: AgentRegistry
    lessons_distiller: LessonsDistiller
    retirement_ceremony: RetirementCeremony
    adam: MemberRuntime
    gateway_registry: ExternalGatewayRegistry
    adapter_manager: AdapterManager
    mail_delivery: MailDelivery
    github_adapter: GitHubAdapter | None

    # domain facades（server 的注入面）
    members: MembersFacade
    mail: MailFacade
    prototypes: PrototypesFacade
    observability: ObservabilityFacade
    configs: ConfigFacade
    events: EventsFacade

    async def shutdown(self) -> None:
        """Shut down all runtime components in reverse order of creation.

        Adam (PID 1) stops before the registry (kernel) it drives. The
        process-wide tracing/metrics pillars are NOT touched here — they
        belong to the process orchestrator (ServerApp), so one runtime's
        shutdown never kills a co-resident runtime's observability.
        """
        await self.adam.shutdown()
        if self.github_adapter:
            await self.github_adapter.shutdown()
        await self.agent_registry.shutdown()
        await self.lessons_distiller.shutdown()
        await self.retirement_ceremony.shutdown()
        await self.mail_delivery.shutdown()
        await self.metrics_service.shutdown()
        await self.adapter_manager.shutdown()
        await self.event_bus.shutdown()
        logger.info("runtime_stopped")


async def build_runtime(runtime_paths: TlonPaths) -> Runtime:
    """Assemble the tlon runtime: all components + domain facades.

    No network ports are opened here — that is the server's concern.
    Every filesystem access flows through the injected ``runtime_paths``,
    so multiple runtimes with distinct tlon homes can coexist in one
    process.

    Args:
        runtime_paths: path resolver for this runtime's tlon home

    Returns:
        The assembled Runtime container
    """
    # ========== Core infrastructure ==========
    # First-boot seeding (idempotent; before any agent starts):
    # built-in prototypes + skills only — members are never seeded
    # (ADR 0016): the human creates them via WebUI / admin API.
    bootstrap_society(runtime_paths)

    # EventBus - event bus for inter-component communication (create first)
    event_bus = EventBus()
    await event_bus.initialize()
    logger.info("component_initialized", component="event_bus")

    # ConfigService - configuration management (needs EventBus)
    config_service = ConfigService(event_bus=event_bus, tlon_home=runtime_paths.tlon_home())
    await config_service.initialize()

    # MetricsService - metrics pillar (pure event consumer, needs EventBus)
    metrics_service = MetricsService(event_bus=event_bus)
    await metrics_service.initialize()
    logger.info("component_initialized", component="metrics_service")

    # TraceStore - storage for traces (stateful)
    trace_store = FileTraceStore(data_dir=runtime_paths.traces_dir())
    logger.info("component_initialized", component="trace_store")

    # AgentRegistry - agent management.
    # Depends on EventBus and ConfigService (as AgentInfoProvider);
    # the metrics/trace pillars are forwarded into each member's
    # observability tools (read-only, )
    agent_registry = AgentRegistry(
        event_bus=event_bus,
        agent_info_provider=config_service,
        tlon_paths=runtime_paths,
        metrics_service=metrics_service,
        trace_store=trace_store,
    )
    # Register initial agents (by ID, config is looked up from provider)
    for agent_id in config_service.agents:
        agent_registry.register(agent_id)
    await agent_registry.initialize()
    logger.info("agents_loaded", agent_count=len(agent_registry.get_all_ids()))

    # LessonsDistiller — retirement distillation (issue ).
    # Pure event consumer like MetricsService, but layered in agents
    # (it depends on memory + agents): on AGENT_STOPPED it rebuilds
    # the retired member's archive offline and distills lessons to
    # memory/lessons.md. (Registry shutdown stops members directly
    # without publishing AGENT_STOPPED, so server shutdown never
    # triggers distillation — only deliberate stops do.)
    lessons_distiller = LessonsDistiller(event_bus=event_bus, tlon_paths=runtime_paths)
    await lessons_distiller.initialize()
    logger.info("component_initialized", component="lessons_distiller")

    # RetirementCeremony — retirement handover (issue ).
    # Same pure-event-consumer mold as the LessonsDistiller, on the
    # same AGENT_STOPPED trigger: inventory the retiree's archive
    # offline, mail a farewell letter when there is anything to hand
    # over, and always publish the AGENT_RETIRED fact.
    retirement_ceremony = RetirementCeremony(event_bus=event_bus, tlon_paths=runtime_paths)
    await retirement_ceremony.initialize()
    logger.info("component_initialized", component="retirement_ceremony")

    # Adam — built-in lifecycle policy member (PID 1; members.md 阶段 D).
    # Kernel first, PID 1 second: Adam is initialized after the registry
    # and issues the boot start requests. It is a MemberRuntime assembled
    # from the built-in ADAM_SPEC (policy as data; phase C unified runtime).
    # It is a bus member, not an agent — it is not registered in the agent
    # registry or MemberDirectory.
    adam = make_adam(event_bus=event_bus, agent_registry=agent_registry, tlon_paths=runtime_paths)
    await adam.initialize()
    logger.info("component_initialized", component="adam")

    # The guardian-aware provider (issue ): a thin wrapper over
    # ConfigService that additionally resolves "adam" so mail to the
    # built-in guardian is deliverable. One instance feeds both the
    # AdapterManager (speak side) and MailDelivery (delivery side) —
    # they share one RecipientResolver, so adam@tlon.internal resolves
    # in both directions. get_all_ids stays Adam-free: no broadcast,
    # no roster, no enumeration-driven scan sees the guardian.
    guardian_info = GuardianAgentInfoProvider(config_service)
    # The external gateway registry (issue  egress) is a plain
    # data component constructed here so every resolver instance on
    # the delivery path consults the same claim table; gateways
    # (github_adapter) claim their addresses at initialize.
    gateway_registry = ExternalGatewayRegistry()

    # AdapterManager - human adapter lifecycle (one per username)
    adapter_manager = AdapterManager(
        event_bus=event_bus,
        agent_info_provider=guardian_info,
        tlon_paths=runtime_paths,
        external_owner=gateway_registry.owner_of,
    )
    await adapter_manager.initialize()
    logger.info("component_initialized", component="adapter_manager")

    # MailDelivery - mail persistence + per-recipient delivery
    # Depends on EventBus, the guardian-aware provider (agent set +
    # adam, issue ) and AdapterManager (online humans); the
    # router's successor (docs/design/mail.md)
    mail_delivery = MailDelivery(
        event_bus=event_bus,
        agent_info_provider=guardian_info,
        adapter_manager=adapter_manager,
        tlon_paths=runtime_paths,
    )
    await mail_delivery.initialize()
    logger.info("component_initialized", component="mail_delivery")

    # GitHub webhook ingress + egress (issue ): the adapter exists
    # only when a webhook secret is configured; without one there is
    # no endpoint. A missing GITHUB_TOKEN disables egress only — the
    # address claim still routes replies into the gateway mailbox.
    # The adapter is a runtime (organization) component, so it is
    # assembled here; the server only reads ``events.github_enabled``
    # to decide whether the /webhooks/github route exists.
    settings = get_settings()
    github_adapter: GitHubAdapter | None = None
    if settings.github_webhook_secret:
        github_adapter = GitHubAdapter(
            event_bus=event_bus,
            agent_info_provider=config_service,
            gateway_registry=gateway_registry,
            tlon_paths=runtime_paths,
            recipient_id=settings.github_recipient or None,
            github_token=settings.github_token or None,
        )
        await github_adapter.initialize()
        logger.info(
            "component_initialized",
            component="github_adapter",
            recipient=settings.github_recipient or None,
            egress_enabled=bool(settings.github_token),
        )
    else:
        logger.info("github_webhook_not_configured")

    return Runtime(
        event_bus=event_bus,
        config_service=config_service,
        metrics_service=metrics_service,
        trace_store=trace_store,
        agent_registry=agent_registry,
        lessons_distiller=lessons_distiller,
        retirement_ceremony=retirement_ceremony,
        adam=adam,
        gateway_registry=gateway_registry,
        adapter_manager=adapter_manager,
        mail_delivery=mail_delivery,
        github_adapter=github_adapter,
        members=MembersFacade(agent_registry=agent_registry, tlon_paths=runtime_paths),
        mail=MailFacade(adapter_manager=adapter_manager, tlon_paths=runtime_paths),
        prototypes=PrototypesFacade(tlon_paths=runtime_paths),
        observability=ObservabilityFacade(
            trace_store=trace_store,
            metrics_service=metrics_service,
            logs_dir=runtime_paths.logs_dir(),
        ),
        configs=ConfigFacade(config_service=config_service, tlon_paths=runtime_paths),
        events=EventsFacade(
            event_bus=event_bus,
            adapter_manager=adapter_manager,
            github_adapter=github_adapter,
        ),
    )
