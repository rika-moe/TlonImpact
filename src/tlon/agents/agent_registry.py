"""Agent Registry for managing multiple agents.

The registry is pure mechanism (docs/design/members.md, 阶段 D — the
policy/mechanism split):
- Agent assembly and run table (``_start_agent`` / shutdown; the
  capability-set assembly itself lives in ``agents/assembly.py``)
- Execution of lifecycle requests: it subscribes to AGENT_START_REQUEST /
  AGENT_STOP_REQUEST and fulfills them idempotently (wake locks,
  ``_waking`` dedup, run-state re-validation)
- Idle *fact* detection: the monitor (``agents/reaper.py`` ``IdleReaper``)
  publishes AGENT_IDLE for lazy agents (no ``:on :boot`` rule) idle past
  their timeout with no live sessions — deciding what to do with that
  fact is not the registry's job
- Spawn/stop of members (stage 6b, unified member model ADR 0006):
  prototype instantiation under a spawn budget (global activation cap,
  spawn depth, per-session quota — ``agents/spawn.py`` ``SpawnManager``);
  spawned members connect to the registry directly (= fork) and are
  retained on idle unload; explicit permanent exit retires them
- Social public-health facts: the monitor
  (``agents/health.py`` ``PublicHealthMonitor``) detects noisy senders
  (AGENT_NOISY) and scans for orphan mail (MAIL_ORPHAN_SCAN) — facts
  only, no enforcement
- Cron scheduling ("tick is a mail", docs/design/mail.md 「周期任务：tick
  即邮件」): the scheduler
  (``agents/cron.py`` ``CronScheduler``) periodically mails members a
  task from postmaster — built-in entries (the daily digest) plus
  entries declared in ``~/.tlon/cron.yaml``; the mail itself carries the
  work instructions

Policy — which agents should run — lives in Adam (tlon.agents.adam), the
built-in init member: boot set and idle drain are its two decision lines.
Wake-on-mail is not a policy line any more (phase E): the registry
*derives* it from each member's spec — a waiting recipient whose spec
declares ``:on :mail-delivered`` can be activated. Timed sleep requires
its deadline or an administrator wake before ordinary mail can activate it. Lifecycle membership is
spec-declared: the boot set and the lazy set come from each member's
``:on`` rules (the spec is the single authority). The registry never
imports Adam.

Configuration is delegated to AgentInfoProvider (typically ConfigManager).

Architecture:
    AgentRegistry is a Component that depends on:
    - EventBus (for agent event publishing and request subscriptions)
    - AgentInfoProvider (for configuration queries)

    All dependencies are injected via constructor (DAG rule). The
    collaborators (assembly / spawn / reaper) get the registry injected
    back at construction; the import direction stays one-directional
    (agent_registry → assembly/spawn/reaper).
"""

import asyncio
import time
from collections.abc import Coroutine
from contextlib import suppress
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from tlon.agents.assembly import assemble_member_services
from tlon.agents.cron import CronScheduler
from tlon.agents.health import PublicHealthMonitor
from tlon.agents.lifecycle import StartRequest, StopRequest
from tlon.agents.loader import has_frozen_work, load_member_spec, read_member_spec
from tlon.agents.member_runtime import MemberRuntime
from tlon.agents.member_spec import MemberSpec
from tlon.agents.policy import (
    policy_probation_provider,
    policy_solvency_provider,
    policy_tilt_provider,
)
from tlon.agents.reaper import IdleReaper
from tlon.agents.scheduling_store import SchedulingStore
from tlon.agents.spawn import SpawnManager
from tlon.agents.vocabulary import ScriptHostQueries, make_member_builtins
from tlon.base.component import Component
from tlon.base.config import AgentConfig
from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus
from tlon.base.logger import get_logger
from tlon.base.metrics import MetricsService
from tlon.base.paths import TlonPaths
from tlon.base.tracing.trace_context import continue_trace, start_span
from tlon.base.tracing.trace_store import TraceStore
from tlon.lifecycle.ports import StopRejectedError
from tlon.organization.mail.mailbox import Mailbox
from tlon.organization.mail.post_office import PostOffice
from tlon.organization.mail.resolver import MemberAddress
from tlon.organization.mail.store import MailStore
from tlosure.builtins import Builtin

if TYPE_CHECKING:
    from tlon.base.config import AgentInfoProvider

logger = get_logger(__name__)


class RegistryQueries(ScriptHostQueries):
    """The shared ``ScriptHostQueries`` implementation over the registry.

    Used by registry-spawned script members (P5b) and by Adam
    (``agents/adam.py``), which imports it from here — Adam depends on the
    registry, never the reverse (kernel first, PID 1 second; the DAG stays
    acyclic). Explicitly implements the protocol so signature drift fails
    at this definition site. Non-string input safely reads as False.
    """

    def __init__(self, registry: "AgentRegistry"):
        self._registry = registry

    def is_sleeping(self, agent_id: Any) -> bool:
        """Registered (config known) but not running."""
        if not isinstance(agent_id, str) or not agent_id:
            return False
        intent = self._registry._scheduling_intents.get(agent_id)
        return intent is not None and intent[0] == "sleep"

    def is_running(self, agent_id: Any) -> bool:
        """Currently in the run table."""
        return isinstance(agent_id, str) and self._registry.get_agent(agent_id) is not None

    def is_lazy(self, agent_id: Any) -> bool:
        """A member that can sleep / be drained (phase E: spec-derived)."""
        if not isinstance(agent_id, str):
            return False
        return self._registry.is_lazy(agent_id)

    def start_all_candidates(self) -> list[str]:
        """The boot set: members whose spec declares ``:on :boot`` (phase E)."""
        return self._registry.boot_candidates()

    def member_roster(self) -> str:
        """Render the public delivery directory, including offline members."""
        return "\n".join(
            f"{member['name']}（{member['id']}） — {member['address']}"
            for member in self._registry.list_members()
        )


class AgentRegistry(Component):
    """Registry for managing multiple agents — pure mechanism.

    Activation-aware run state (see docs/design/members.md, 阶段 D):
    - The registry owns the run table, per-agent wake locks, and the
      ``_waking`` dedup set, and executes AGENT_START_REQUEST /
      AGENT_STOP_REQUEST idempotently.
    - Waiting lazy members can be unloaded after idle_timeout_seconds.
      Unloading is not timed sleep. Explicit sleep retains a deadline;
      permanent exit retires the mailbox and preserves member history.
    - Which requests get issued is Adam's policy, not the registry's.

    Configuration is provided by AgentInfoProvider (typically ConfigManager),
    allowing this class to focus on agent lifecycle management only.

    Dependencies (constructor injected):
        - event_bus: EventBus for agent event publishing
        - agent_info_provider: AgentInfoProvider for configuration queries

    Example:
        >>> registry = AgentRegistry(
        ...     event_bus=event_bus,
        ...     agent_info_provider=config_manager,
        ... )
        >>> registry.register("dev-001")  # Register by ID
        >>> await registry.initialize()  # Subscribe + start idle monitor
        >>> agent = registry.get_agent("dev-001")  # Get running agent
    """

    # Per-agent assembly timeout (seconds): a hung start must not wedge
    # the agent's wake lock forever (restores the old boot-loop guard).
    _START_TIMEOUT_SECONDS = 60

    # Pending-stop watcher poll interval (seconds): a stop registered with
    # when_idle=true fires as soon as the member's sessions drain — it
    # must not wait for the idle reaper's timeout.
    _PENDING_STOP_POLL_SECONDS = 1.0

    def __init__(
        self,
        event_bus: EventBus,
        agent_info_provider: "AgentInfoProvider",
        tlon_paths: "TlonPaths",
        max_activations: int = 8,
        max_spawn_depth: int = 3,
        session_spawn_quota: int = 2,
        metrics_service: MetricsService | None = None,
        trace_store: TraceStore | None = None,
        post_office: PostOffice | None = None,
    ):
        """Initialize the agent registry.

        All dependencies are injected via constructor (DAG architecture).

        Args:
            event_bus: Event bus for mail delivery and lifecycle events
            agent_info_provider: Provider for agent configuration queries
            tlon_paths: Path resolver for the runtime's filesystem layout
            max_activations: spawn budget — cap on running agents total
                (boot-set members included); spawning beyond it is rejected
            max_spawn_depth: spawn budget — a spawned member's depth is
                spawner.depth + 1; exceeding this rejects the spawn
            session_spawn_quota: spawn budget — max spawns per
                (spawner, session) pair
            metrics_service: Metrics pillar injected into each member's
                observability tools (read-only snapshot); None
                skips the metrics-read tool
            trace_store: Trace pillar injected into each member's
                observability tools (read-only); None skips the
                traces-list/trace-read tools
        """
        super().__init__(name="AgentRegistry")
        self.event_bus = event_bus
        self._agent_info = agent_info_provider
        self._paths = tlon_paths
        self._scheduling_store = SchedulingStore(tlon_paths.mail_database_file())
        self._scheduling_intents: dict[str, tuple[str, str | None, str]] = {}
        self._sleep_tasks: dict[str, asyncio.Task[None]] = {}
        self._dead: set[str] = set()
        self._terminating: set[str] = set()
        self._post_office = post_office
        self._mail_rescue_task: asyncio.Task[None] | None = None
        self._mail_rescue_not_before: dict[str, float] = {}
        self._lifecycle_tasks: set[asyncio.Task[None]] = set()
        self._shutting_down = False
        self._metrics_service = metrics_service
        self._trace_store = trace_store

        # Spawn budget + idle reaper (stage 6b / scale-to-zero). The
        # per-session quota ledger is persisted (sessions survive a
        # restart via journal replay, so the anti-runaway cap must too).
        # The quota is tilted per spawn by the target bloodline's fitness
        # (ADR 0014), gated by its solvency (ADR 0014), and capped at 1
        # when the spawner itself is on probation (trust ladder,
        # docs/design/unified-member-model.md); all
        # providers recompute from raw files on every spawn.
        self._spawn = SpawnManager(
            self,
            max_activations,
            max_spawn_depth,
            session_spawn_quota,
            tilt_provider=policy_tilt_provider(self._paths),
            solvency=policy_solvency_provider(self._paths),
            probation=policy_probation_provider(self._paths),
        )
        self._reaper = IdleReaper(self)
        # Social public-health monitor: noise detection + orphan-mail
        # scan. Facts only — no enforcement. Same ownership as the reaper.
        self._health = PublicHealthMonitor(
            event_bus=event_bus,
            agent_info_provider=agent_info_provider,
            tlon_paths=tlon_paths,
        )
        # Cron scheduler (docs/design/mail.md 「周期任务：tick 即邮件」):
        # "tick is a mail" — periodic task mails
        # from postmaster (built-in digest + declared cron.yaml entries).
        # Same ownership.
        self._cron = CronScheduler(
            post_office=post_office,
            event_bus=event_bus,
            agent_info_provider=agent_info_provider,
            tlon_paths=tlon_paths,
        )

        # Registered agent IDs (persona full set; may or may not be running)
        self._registered_ids: list[str] = []

        # Running agents (activations): one unified member runtime — all
        # expose shutdown / get_state / active_session_count / idle_seconds
        self._agents: dict[str, MemberRuntime] = {}

        # Docker runtimes are built during session assembly, keyed by
        # agent id — but the registry deliberately does NOT hold handles
        # for reclaim: a sleeping member keeps its container (zero-cost
        # wake / lazy reuse on next boot, unified member model — members
        # never have their home or container force-removed, ADR 0006).
        # A re-assembly reuses the container by name.

        # Request execution state: agents currently being started, and one
        # lock per agent serializing start/drain of the same persona
        self._waking: set[str] = set()
        self._wake_locks: dict[str, asyncio.Lock] = {}

        # Pending stops (member-stop when_idle=true): agent_id → stopped_by,
        # with one watcher task per entry. In-memory only — a pending stop
        # is a one-shot intent tied to the current activation; across a
        # restart the target's session state changes anyway and the caller
        # can re-issue, so persistence would add nothing ( precedent:
        # transient runtime intents are not journaled).
        self._pending_stops: dict[str, str] = {}
        self._pending_stop_tasks: dict[str, asyncio.Task[None]] = {}

        # Lifecycle request subscriptions + idle monitor task
        self._request_subscriptions: list[str] = []
        self._idle_monitor_task: asyncio.Task[None] | None = None

        # Spec-derived lifecycle profile cache (phase E): per registered
        # member, the loaded MemberSpec or None (unreadable). The boot set,
        # the lazy set, and wake eligibility all derive from here — the
        # spec is the single lifecycle authority. None is cached too so a
        # broken spec is read once, not on every boot/status/wake query;
        # adopt/reload invalidates the entry to retry.
        self._profiles: dict[str, MemberSpec | None] = {}

        # Host builtins validated against — shared by the profile loader and
        # ``_start_agent`` so a member's spec passes the same gate whether
        # loaded for derivation or for assembly.
        self._host_builtins = make_member_builtins(RegistryQueries(self))

        logger.info("agent_registry_constructed")

    def _member_builtins(self) -> dict[str, Builtin]:
        """Return host contracts used to validate member behavior."""
        return dict(self._host_builtins)

    def register(self, agent_id: str) -> None:
        """Register an agent to be started.

        The agent configuration is looked up from AgentInfoProvider.
        The agent will be started during initialize().

        Args:
            agent_id: The agent ID to register

        Raises:
            ValueError: If agent configuration is not found
        """
        if not self._agent_info.get_config(agent_id):
            raise ValueError(f"Cannot register unknown agent: {agent_id}")

        if agent_id not in self._registered_ids:
            self._registered_ids.append(agent_id)
            logger.info("agent_registered", agent_id=agent_id)

    def register_all(self, agent_ids: list[str]) -> None:
        """Register multiple agents.

        Args:
            agent_ids: List of agent IDs
        """
        for agent_id in agent_ids:
            self.register(agent_id)

    # ==================== Spec-derived lifecycle (phase E) ====================

    def _load_activation_spec(self, agent_id: str, builtins: dict[str, Builtin]) -> MemberSpec:
        if has_frozen_work(agent_id, self._paths):
            return read_member_spec(agent_id, tlon_paths=self._paths)
        return load_member_spec(agent_id, tlon_paths=self._paths, builtins=builtins)

    def _spec_profile(self, agent_id: str) -> MemberSpec | None:
        """Load (and cache) a member's validated spec, or None if unreadable.

        The profile is the source of boot-set, lazy-set, and wake
        eligibility. It is validated against the same host builtins as
        ``_start_agent`` so a spec that uses host predicates (``sleeping?``
        etc.) derives consistently. A failed load is cached as None with a
        single warning — the entry is invalidated by adopt/reload so a fixed
        file is retried.
        """
        cached = self._profiles.get(agent_id)
        if cached is not None:
            return cached
        if agent_id in self._profiles:
            return None  # cached failure
        try:
            spec = self._load_activation_spec(agent_id, self._member_builtins())
        except (FileNotFoundError, ValueError, OSError) as e:
            logger.warning("member_spec_unreadable", agent_id=agent_id, error=str(e))
            self._profiles[agent_id] = None
            return None
        self._profiles[agent_id] = spec
        return spec

    def boot_candidates(self) -> list[str]:
        """The boot set (phase E): registered members whose spec declares
        ``:on :boot``, not already running.

        A member whose spec cannot be loaded has no boot rule to declare and
        is excluded — the spec is the single lifecycle authority.
        """
        candidates: list[str] = []
        for agent_id in self._registered_ids:
            spec = self._spec_profile(agent_id)
            if spec is None or not any(rule.on is None for rule in spec.rules):
                continue
            if self._agents.get(agent_id) is not None:
                continue
            candidates.append(agent_id)
        return candidates

    def is_lazy(self, agent_id: str) -> bool:
        """A member that can sleep / be drained (spec-derived, ADR 0006).

        A member is lazy iff its spec has no ``:on :boot`` rule — or its
        spec cannot be loaded, in which case it is inert (registered
        only). Laziness is a spec fact, never a birth marker: a spawned
        member whose prototype carries a boot rule is a boot member
        and starts at boot like any other.
        """
        spec = self._spec_profile(agent_id)
        if spec is None:
            return True
        return not any(rule.on is None for rule in spec.rules)

    async def initialize(self) -> None:
        """Initialize the registry: subscribe to requests, start the monitor.

        Pure mechanism: no agent is started here. Wake-on-mail is now a
        mechanized policy — the registry derives wake eligibility from each
        member's spec (``:on :mail-delivered``) and issues its own
        AGENT_START_REQUEST{reason="wake"}; boot remains Adam's policy (it
        issues AGENT_START_REQUEST after its own initialize, asynchronous
        over the bus). Agents that fail to start are logged by the request
        fulfillment path — the server continues operating with the agents
        that did start.
        """
        self._shutting_down = False
        self._scheduling_intents = await asyncio.to_thread(self._scheduling_store.load)
        for member_id, (action, wake_at, origin) in list(self._scheduling_intents.items()):
            if action == "exit":
                await asyncio.to_thread(
                    MailStore(self._paths.mail_database_file()).retire, member_id
                )
                self._dead.add(member_id)
                await asyncio.to_thread(self._scheduling_store.delete, member_id)
                self._scheduling_intents.pop(member_id, None)
            elif action == "wait" or origin.startswith("admin:pending:"):
                await asyncio.to_thread(self._scheduling_store.delete, member_id)
                self._scheduling_intents.pop(member_id, None)
            elif wake_at is not None:
                self._schedule_deadline(member_id, wake_at)
        logger.info("agent_registry_initializing", agent_ids=self._registered_ids)

        # Lifecycle request subscriptions (idempotent across re-initialize)
        if not self._request_subscriptions:
            self._request_subscriptions.append(
                self.event_bus.subscribe(EventType.AGENT_START_REQUEST, self._on_start_request)
            )
            self._request_subscriptions.append(
                self.event_bus.subscribe(EventType.AGENT_STOP_REQUEST, self._on_stop_request)
            )
            self._request_subscriptions.append(
                self.event_bus.subscribe(EventType.MAIL_DELIVERED, self._on_mail_delivered)
            )

        # Idle fact monitor (idempotent across re-initialize)
        if self._idle_monitor_task is None:
            self._idle_monitor_task = asyncio.create_task(self._reaper.run())

        if self._mail_rescue_task is None:
            self._mail_rescue_task = asyncio.create_task(self._rescue_mail_loop())

        # Public-health monitor (noise subscription + orphan scan task)
        await self._health.initialize()

        # Cron scheduler (periodic task mails)
        await self._cron.initialize()

        self._initialized = True
        logger.info(
            "agent_registry_initialized",
            active_count=len(self._agents),
            registered_ids=self._registered_ids,
        )

    async def shutdown(self) -> None:
        """Shutdown all agents and cleanup."""
        self._shutting_down = True
        sleepers = list(self._sleep_tasks.values())
        for task in sleepers:
            task.cancel()
        if sleepers:
            await asyncio.gather(*sleepers, return_exceptions=True)
        self._sleep_tasks.clear()
        logger.info("agent_registry_shutdown_started")

        if self._mail_rescue_task is not None:
            self._mail_rescue_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._mail_rescue_task
            self._mail_rescue_task = None

        # Stop the idle monitor first so it cannot drain mid-shutdown
        if self._idle_monitor_task:
            self._idle_monitor_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._idle_monitor_task
            self._idle_monitor_task = None

        tasks = list(self._lifecycle_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._lifecycle_tasks.clear()
        self._mail_rescue_not_before.clear()

        # Stop the public-health monitor (scan task + MAIL_NEW subscription)
        await self._health.shutdown()

        # Stop the cron scheduler
        await self._cron.shutdown()

        # Pending stops die with the process (in-memory one-shot intents)
        for task in list(self._pending_stop_tasks.values()):
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        self._pending_stop_tasks.clear()
        self._pending_stops.clear()

        # Stop lifecycle request subscriptions
        for subscription in self._request_subscriptions:
            self.event_bus.unsubscribe(subscription)
        self._request_subscriptions.clear()

        # Shutdown all active agents
        for agent_id, agent in list(self._agents.items()):
            try:
                await agent.shutdown()
                logger.info("agent_shutdown", agent_id=agent_id)
            except Exception as e:
                logger.error("agent_shutdown_failed", agent_id=agent_id, error=str(e))

        self._agents.clear()
        self._registered_ids.clear()
        self._waking.clear()
        self._spawn.reset()
        self._profiles.clear()  # phase E: spec-derived lifecycle profiles
        self._initialized = False

        logger.info("agent_registry_shutdown_completed")

    async def _start_agent(self, agent_id: str) -> MemberRuntime | None:
        """Assemble and start one agent (unified member model, ADR 0006).

        This is the sole assembler for members and their infrastructure.
        Every member gets its assembled services (``assemble_member_services`` in
        ``agents/assembly.py``: LLM client, memory, task manager, Docker
        runtime, tools). The same rule executor handles all members;
        session services are used only when a program requests them.

        Args:
            agent_id: ID of the agent to start

        Returns:
            The started MemberRuntime instance, or None if not found
        """
        if not self.can_schedule(agent_id):
            return None
        if await asyncio.to_thread(
            MailStore(self._paths.mail_database_file()).is_retired, agent_id
        ):
            logger.warning("retired_member_start_rejected", agent_id=agent_id)
            return None
        if agent_id in self._agents:
            # Already active
            return self._agents[agent_id]

        config = self._agent_info.get_config(agent_id)
        if not config:
            logger.error("unknown_agent", agent_id=agent_id)
            return None

        member: MemberRuntime | None = None
        try:
            logger.info("agent_starting", agent_id=agent_id, agent_name=config.name)

            # Unified assembly (ADR 0006): every member gets the full
            # capability set. Rules request session execution when needed.
            # Host builtins gate
            # validation too (D6.6: host predicates open to registry
            # members).
            builtins = self._member_builtins()
            spec = self._load_activation_spec(agent_id, builtins)
            self._profiles[agent_id] = spec  # cache the profile (phase E)
            mailbox = Mailbox(self._paths.mail_database_file(), config.id)
            services = await assemble_member_services(
                config,
                registry=self,
                agent_info=self._agent_info,
                tlon_paths=self._paths,
                metrics_service=self._metrics_service,
                trace_store=self._trace_store,
            )

            member = MemberRuntime(
                member_id=config.id,
                name=config.name,
                event_bus=self.event_bus,
                spec=spec,
                builtins=self._host_builtins,
                mailbox=mailbox,
                services=services,
                registry=self,
                post_office=self._post_office,
            )
            await member.initialize()

            # Track active agent
            self._agents[agent_id] = member

            # Publish agent started event
            await self.event_bus.publish(
                Event(
                    type=EventType.AGENT_STARTED,
                    payload={
                        "agent_id": agent_id,
                        "name": config.name,
                        "spec_hash": spec.source_hash,
                    },
                    source="agent_registry",
                )
            )

            logger.info(
                "agent_started",
                agent_id=agent_id,
                agent_name=config.name,
                spec_hash=spec.source_hash,
            )
            return member

        except asyncio.CancelledError:
            if member is not None:
                await member.shutdown()
            raise
        except Exception as e:
            logger.error("agent_start_failed", agent_id=agent_id, error=str(e), exc_info=True)
            return None

    # ==================== Lifecycle request execution ====================

    async def _rescue_mail_loop(self) -> None:
        """Durable unread mail wakes sleepers even when a notification was lost."""
        while True:
            try:
                await self._rescue_sleeping_mail()
            except Exception:
                logger.exception("mail_rescue_failed")
            await asyncio.sleep(30)

    async def _rescue_sleeping_mail(self) -> None:
        store = MailStore(self._paths.mail_database_file())
        for member_id in await asyncio.to_thread(store.unread_member_ids):
            if time.monotonic() < self._mail_rescue_not_before.get(member_id, 0):
                continue
            await self._on_mail_delivered(
                Event(
                    type=EventType.MAIL_DELIVERED,
                    payload={"recipient": member_id},
                    source="mail_rescue",
                )
            )

    def defer_mail_rescue(self, agent_id: str) -> None:
        """Respect the member's own rescan cadence after idle sleep.

        This throttles recovery only. A genuinely new delivery notification
        still wakes the member immediately, and failed wake attempts remain
        eligible for retry on the next recovery poll.
        """
        config = self._agent_info.get_config(agent_id)
        if config is not None:
            self._mail_rescue_not_before[agent_id] = (
                time.monotonic() + config.mail_rescan_interval_seconds
            )

    async def _on_mail_delivered(self, event: Event) -> None:
        """Wake-on-mail (phase E): issue a wake for a sleeping subscriber.

        Mechanized policy — the spec is the single lifecycle authority:
        a MAIL_DELIVERED whose recipient is registered, not running/waking,
        and whose spec declares ``:on :mail-delivered`` gets an
        AGENT_START_REQUEST{reason="wake"} that joins the delivery's trace
        tree (``_fulfill_wake`` continues the same trace). The mail is
        already durably in the member's inbox, so no trigger event is
        carried — the woken member scans unread mail on boot
        (docs/design/mail.md). Previously this was Adam's wake rule; the
        decision now lives here so lifecycle policy is spec-derived.
        """
        recipient = event.payload.get("recipient")
        if not isinstance(recipient, str):
            return
        if not self.can_schedule(recipient):
            return
        if await asyncio.to_thread(
            MailStore(self._paths.mail_database_file()).is_retired, recipient
        ):
            return
        if self._agents.get(recipient) is not None or recipient in self._waking:
            return  # already running or waking — no request needed
        if self._agent_info.get_config(recipient) is None:
            return  # unknown member
        spec = self._spec_profile(recipient)
        if spec is None or not any(rule.on is EventType.MAIL_DELIVERED for rule in spec.rules):
            return  # spec does not subscribe to mail delivery
        await self.event_bus.publish(
            Event(
                type=EventType.AGENT_START_REQUEST,
                payload=StartRequest(agent_id=recipient, reason="wake"),
                source="agent_registry",
                trace_id=event.trace_id,
                span_id=event.span_id,
                parent_span_id=event.parent_span_id,
            )
        )

    def _launch_lifecycle(self, coroutine: Coroutine[object, object, Any]) -> None:
        task = asyncio.create_task(coroutine)
        self._lifecycle_tasks.add(task)
        task.add_done_callback(self._lifecycle_done)

    def _lifecycle_done(self, task: asyncio.Task[None]) -> None:
        self._lifecycle_tasks.discard(task)
        if not task.cancelled() and (error := task.exception()) is not None:
            logger.error("lifecycle_task_failed", error=str(error))

    async def _on_start_request(self, event: Event) -> None:
        """Execute an AGENT_START_REQUEST decision (mechanism only).

        Runs in the EventBus dispatch context, so it must stay fast: the
        actual assembly happens in a background task. Idempotent — a
        running or already-waking agent, or an unknown id, is skipped.
        The dedup state is updated synchronously here so back-to-back
        requests for the same persona collapse to one assembly.
        """
        if self._shutting_down:
            return
        request = event.payload
        if not isinstance(request, StartRequest):
            return

        agent_id = request.agent_id
        if agent_id in self._agents or agent_id in self._waking:
            return
        if not self._agent_info.get_config(agent_id):
            return
        self._waking.add(agent_id)
        self._launch_lifecycle(self._fulfill_start_request(request, event))

    async def _fulfill_start_request(self, request: StartRequest, event: Event) -> None:
        """Assemble one agent under its wake lock, per the request's reason.

        Serialized per agent by a lock: concurrent requests for the same
        persona trigger exactly one assembly.

        reason="wake": publishes AGENT_WAKING / AGENT_WOKEN /
        AGENT_WAKE_FAILED joining the delivering event's trace tree. There
        is no redelivery: the mail is already in the agent's inbox and the
        freshly started agent scans its unread mail on boot — the mailbox
        is the queue (docs/design/mail.md).

        reason="boot": starts the agent with log lines only (no
        observability events) — same as boot always did.
        """
        agent_id = request.agent_id
        lock = self._wake_locks.setdefault(agent_id, asyncio.Lock())
        try:
            async with lock:
                if request.reason == "wake":
                    await self._fulfill_wake(agent_id, event)
                else:
                    await self._fulfill_boot(agent_id, request.reason)
        finally:
            self._waking.discard(agent_id)

    async def _start_agent_guarded(self, agent_id: str) -> MemberRuntime | None:
        """``_start_agent`` with a timeout: a hung assembly must not wedge
        the per-agent wake lock forever. Returns None on timeout (same
        contract as ``_start_agent`` failure)."""
        try:
            return await asyncio.wait_for(
                self._start_agent(agent_id), timeout=self._START_TIMEOUT_SECONDS
            )
        except TimeoutError:
            logger.error(
                "agent_start_timeout",
                agent_id=agent_id,
                timeout_seconds=self._START_TIMEOUT_SECONDS,
            )
            return None

    async def _fulfill_boot(self, agent_id: str, reason: str) -> None:
        """Start an agent without wake observability (boot path)."""
        started_at = time.monotonic()
        logger.info("agent_starting", agent_id=agent_id, reason=reason)
        agent = await self._start_agent_guarded(agent_id)
        if agent is None:
            logger.error("agent_start_failed", agent_id=agent_id, reason=reason)
            return
        logger.info(
            "agent_started",
            agent_id=agent_id,
            reason=reason,
            start_duration_seconds=round(time.monotonic() - started_at, 3),
        )

    async def _fulfill_wake(self, agent_id: str, request_event: Event) -> None:
        """Wake a sleeping agent; its boot scan picks up the waiting mail."""
        started_at = time.monotonic()
        logger.info("agent_waking", agent_id=agent_id)
        await self.event_bus.publish(
            Event(
                type=EventType.AGENT_WAKING,
                payload={"agent_id": agent_id},
                source="agent_registry",
                trace_id=request_event.trace_id,
                span_id=request_event.span_id,
                parent_span_id=request_event.parent_span_id,
            )
        )

        # Join the mail's trace tree so wake cost is visible as an
        # "agent.wake" span in the same trace.
        if request_event.trace_id:
            with (
                continue_trace(request_event.trace_id, request_event.span_id),
                start_span(
                    "agent.wake",
                    source="agent_registry",
                    attributes={"agent_id": agent_id},
                ),
            ):
                agent = await self._start_agent_guarded(agent_id)
        else:
            with start_span(
                "agent.wake",
                source="agent_registry",
                attributes={"agent_id": agent_id},
            ):
                agent = await self._start_agent_guarded(agent_id)

        wake_duration = time.monotonic() - started_at

        if agent is None:
            logger.error("agent_wake_failed", agent_id=agent_id)
            await self.event_bus.publish(
                Event(
                    type=EventType.AGENT_WAKE_FAILED,
                    payload={"agent_id": agent_id},
                    source="agent_registry",
                    trace_id=request_event.trace_id,
                    span_id=request_event.span_id,
                    parent_span_id=request_event.parent_span_id,
                )
            )
            return

        logger.info(
            "agent_woken",
            agent_id=agent_id,
            wake_duration_seconds=round(wake_duration, 3),
        )
        await self.event_bus.publish(
            Event(
                type=EventType.AGENT_WOKEN,
                payload={
                    "agent_id": agent_id,
                    "wake_duration_seconds": round(wake_duration, 3),
                },
                source="agent_registry",
                trace_id=request_event.trace_id,
                span_id=request_event.span_id,
                parent_span_id=request_event.parent_span_id,
            )
        )

    async def _on_stop_request(self, event: Event) -> None:
        """Execute an AGENT_STOP_REQUEST decision (mechanism only).

        Runs in the EventBus dispatch context; the drain itself (agent
        shutdown) happens in a background task.
        """
        request = event.payload
        if not isinstance(request, StopRequest):
            return
        if not self._shutting_down:
            self._launch_lifecycle(self._reaper.drain(request.agent_id, request.reason))

    # ==================== Hot adoption + spawn (stage 6b) ====================

    async def adopt_agent(self, agent_id: str) -> str:
        """Adopt an agent created out-of-process (WebUI/ops), no restart.

        Delegates to ``agents/spawn.py`` ``SpawnManager``; see there for
        semantics and the result enum.
        """
        return await self._spawn.adopt_agent(agent_id)

    async def spawn_member(
        self,
        prototype: str,
        name: str,
        spawner_id: str,
        session_id: str | None,
        params: dict[str, Any] | None = None,
    ) -> AgentConfig:
        """Spawn a new member from a prototype and start it immediately.

        Delegates to ``agents/spawn.py`` ``SpawnManager`` (budget checks,
        instantiation, lineage, AGENT_SPAWNED); see there for semantics.

        Raises:
            SpawnRejectedError: a budget cap was hit (see ``.reason``)
            PrototypeError: prototype missing
        """
        return await self._spawn.spawn_member(prototype, name, spawner_id, session_id, params)

    async def stop_member(self, agent_id: str, stopped_by: str, when_idle: bool = False) -> bool:
        """Retire a member after draining; future mail and starts are rejected.

        ADR 0030: explicit stop retires the mailbox permanently without
        deleting history. Idle drain unloads a waiting instance.
        Drain semantics mirror the idle reaper: a member with any live
        (ACTIVE/WAITING) session is never stopped. Whether a stop request
        is accepted is policy (decision 7); the registry executes requests
        idempotently and re-checks drain conditions under the lock.

        Pending semantics (``when_idle=True``): instead of rejecting a
        member with live sessions, register a "stop when idle" intent —
        a watcher fires the stop the moment the sessions drain (it does
        not wait for the idle timeout). Registration is idempotent:
        re-registering just refreshes the audit ``stopped_by``. A member
        that wakes back up during the wait simply keeps the watcher
        waiting — the intent is condition-based, not a timer.

        Args:
            agent_id: member to stop
            stopped_by: agent id requesting the stop (audit)
            when_idle: register a pending stop instead of rejecting when
                the member has live sessions

        Returns:
            True when the member was retired; False when a
            pending stop was registered instead (``when_idle=True`` only).

        Raises:
            StopRejectedError: unknown agent / live sessions without
                ``when_idle``
        """
        config = self._agent_info.get_config(agent_id)
        if config is None:
            raise StopRejectedError("unknown_agent", f"agent '{agent_id}' not found")

        # Retirement is durable before cleanup, and cleanup must remain
        # retryable even when a previous cancellation already committed it.
        lock = self._wake_locks.setdefault(agent_id, asyncio.Lock())
        async with lock:
            self._terminating.add(agent_id)
            try:
                retired = await asyncio.to_thread(
                    MailStore(self._paths.mail_database_file()).is_retired, agent_id
                )
                agent = self._agents.get(agent_id)
                if not retired and agent is not None and agent.active_session_count > 0:
                    if when_idle:
                        self._register_pending_stop(agent_id, stopped_by)
                        return False
                    raise StopRejectedError(
                        "active_sessions",
                        f"agent '{agent_id}' has {agent.active_session_count} live session(s); "
                        "wait for them to finish (drain), or stop with when_idle=true",
                    )
                if not retired:
                    await asyncio.to_thread(
                        MailStore(self._paths.mail_database_file()).retire, agent_id
                    )
                self._dead.add(agent_id)
                await asyncio.to_thread(self._scheduling_store.delete, agent_id)
                self._scheduling_intents.pop(agent_id, None)
                timer = self._sleep_tasks.pop(agent_id, None)
                if timer is not None:
                    timer.cancel()
                if agent is not None:
                    # Keep the live reference on cancellation or failure. A
                    # retired marker alone must never short-circuit this work.
                    await agent.shutdown()
                    self._agents.pop(agent_id, None)
                if not retired or agent is not None:
                    await self.event_bus.publish(
                        Event(
                            type=EventType.AGENT_STOPPED,
                            payload={"agent_id": agent_id, "stopped_by": stopped_by},
                            source="agent_registry",
                        )
                    )
            finally:
                self._terminating.discard(agent_id)

        # The stop fulfills any pending intent for this member (cancel the
        # watcher outside the wake lock; never cancel ourselves — the
        # watcher calls stop_member to fire its intent).
        self._pending_stops.pop(agent_id, None)
        watcher = self._pending_stop_tasks.pop(agent_id, None)
        if watcher is not None and watcher is not asyncio.current_task():
            watcher.cancel()
            with suppress(asyncio.CancelledError):
                await watcher

        await self.publish_scheduling_state(agent_id)
        logger.info("agent_stopped", agent_id=agent_id, stopped_by=stopped_by)
        return True

    def _register_pending_stop(self, agent_id: str, stopped_by: str) -> None:
        """Register a "stop when idle" intent and start its watcher.

        Idempotent: a repeated registration refreshes the audit
        ``stopped_by`` and reuses the existing watcher task.
        """
        self._pending_stops[agent_id] = stopped_by
        if agent_id in self._pending_stop_tasks:
            return
        logger.info("pending_stop_registered", agent_id=agent_id, stopped_by=stopped_by)
        self._pending_stop_tasks[agent_id] = asyncio.create_task(self._watch_pending_stop(agent_id))

    async def _watch_pending_stop(self, agent_id: str) -> None:
        """Fire a pending stop the moment the member's sessions drain.

        Polls ``active_session_count`` (the same criterion ``stop_member``
        drains on) and delegates the actual stop to ``stop_member`` so the
        drain re-check under the wake lock stays the single guard. The
        watcher exits when the intent is cancelled (stopped by other
        means), fulfilled (member asleep), or the member is gone from the
        config; a member that wakes back up during the wait just keeps the
        watcher waiting.
        """
        try:
            while True:
                await asyncio.sleep(self._PENDING_STOP_POLL_SECONDS)
                stopped_by = self._pending_stops.get(agent_id)
                if stopped_by is None:
                    return  # cancelled — stopped (or removed) by other means
                agent = self._agents.get(agent_id)
                if agent is None:
                    # Idle unload does not retire the member: finish the exit intent.
                    await self.stop_member(agent_id, stopped_by)
                    return
                if agent.active_session_count > 0:
                    continue
                try:
                    stopped_now = await self.stop_member(
                        agent_id, stopped_by=stopped_by, when_idle=True
                    )
                except StopRejectedError as e:
                    # Only unknown_agent can reach here (the member left
                    # the config); the intent is unfulfillable.
                    logger.warning("pending_stop_dropped", agent_id=agent_id, reason=e.reason)
                    return
                if stopped_now:
                    logger.info("pending_stop_fired", agent_id=agent_id)
                    return
                # A session slipped into the race window: stop_member
                # re-registered the intent — keep watching.
        finally:
            self._pending_stop_tasks.pop(agent_id, None)

    async def remove_member(self, agent_id: str, removed_by: str) -> bool:
        """Administrative removal of a member (wizard removal, ADR 0006).

        Distinct from ``stop_member`` (permanent exit retaining history):
        this deletes the member's records — its running instance is
        shut down, it is unregistered, and its home directory is
        deleted by the config provider. The runtime never does this on
        its own; it is the explicit management action behind the admin
        DELETE API.

        Args:
            agent_id: member to remove
            removed_by: who requested the removal (audit)

        Returns:
            True when the member was removed.

        Raises:
            StopRejectedError: unknown agent / live sessions
        """
        config = self._agent_info.get_config(agent_id)
        if config is None:
            raise StopRejectedError("unknown_agent", f"agent '{agent_id}' not found")

        # Serialize with a concurrent wake of the same persona
        lock = self._wake_locks.setdefault(agent_id, asyncio.Lock())
        async with lock:
            agent = self._agents.pop(agent_id, None)
            if agent is not None:
                if agent.active_session_count > 0:
                    self._agents[agent_id] = agent
                    raise StopRejectedError(
                        "active_sessions",
                        f"agent '{agent_id}' has live sessions; stop it first",
                    )
                try:
                    await agent.shutdown()
                except asyncio.CancelledError:
                    self._agents[agent_id] = agent
                    raise
                except Exception as e:
                    logger.error("agent_stop_failed", agent_id=agent_id, error=str(e))

            await asyncio.to_thread(MailStore(self._paths.mail_database_file()).retire, agent_id)
            if agent_id in self._registered_ids:
                self._registered_ids.remove(agent_id)
            self._profiles.pop(agent_id, None)
            self._mail_rescue_not_before.pop(agent_id, None)
            self._wake_locks.pop(agent_id, None)
            try:
                self._agent_info.delete_agent(agent_id)
            except ValueError:
                logger.warning("agent_home_missing", agent_id=agent_id)

        logger.info("agent_removed", agent_id=agent_id, removed_by=removed_by)
        await self.event_bus.publish(
            Event(
                type=EventType.AGENT_STOPPED,
                payload={
                    "agent_id": agent_id,
                    "stopped_by": removed_by,
                    "removed": True,
                },
                source="agent_registry",
            )
        )
        return True

    # ==================== Idle drain execution ====================

    async def _drain_agent(self, agent_id: str, reason: str) -> None:
        """Execute a stop request: drain one idle lazy agent.

        Delegates to ``agents/reaper.py`` ``IdleReaper.drain`` — the
        condition is re-checked under the per-agent lock right before
        shutdown.
        """
        await self._reaper.drain(agent_id, reason)

    # ==================== State queries ====================

    def get_agent(self, agent_id: str) -> MemberRuntime | None:
        """Get an active agent by ID.

        Args:
            agent_id: Agent ID

        Returns:
            The MemberRuntime instance if active, None otherwise
        """
        return self._agents.get(agent_id)

    def get_all_agents(self) -> list[MemberRuntime]:
        """Get all currently active agents.

        Returns:
            List of active MemberRuntime instances
        """
        return list(self._agents.values())

    def list_members(self) -> list[MemberAddress]:
        """Query the delivery layer without changing lifecycle enumeration."""
        if self._post_office is None:
            raise RuntimeError("member directory requires a post office")
        return self._post_office.list_members()

    def get_all_ids(self) -> list[str]:
        """Get all registered agent IDs.

        Returns:
            List of all agent IDs from AgentInfoProvider
        """
        return list(self._agent_info.get_all_ids())

    def get_config(self, agent_id: str) -> AgentConfig | None:
        """Get an agent's configuration.

        Delegates to AgentInfoProvider.

        Args:
            agent_id: Agent ID

        Returns:
            AgentConfig or None if not found
        """
        return self._agent_info.get_config(agent_id)

    async def publish_scheduling_state(self, member_id: str) -> None:
        """Publish the same authoritative snapshot served by the status API."""
        status = await self.get_agent_status(member_id)
        if status is not None:
            await self.event_bus.publish(
                Event(
                    type=EventType.AGENT_STATE_CHANGED,
                    payload={"agent_id": member_id, **status},
                    source="agent_registry",
                )
            )

    def can_schedule(self, member_id: str) -> bool:
        """One admission gate shared by delivery, scans, reflection and DSL."""
        return (
            member_id not in self._dead
            and member_id not in self._terminating
            and member_id not in self._pending_stops
            and member_id not in self._scheduling_intents
            and not self._shutting_down
        )

    async def _require_live_member(self, member_id: str) -> None:
        if self._agent_info.get_config(member_id) is None:
            raise StopRejectedError("unknown_agent", f"agent '{member_id}' not found")
        intent = self._scheduling_intents.get(member_id)
        retired = await asyncio.to_thread(
            MailStore(self._paths.mail_database_file()).is_retired, member_id
        )
        if (
            retired
            or member_id in self._dead
            or member_id in self._pending_stops
            or member_id in self._terminating
            or (intent is not None and intent[0] == "exit")
        ):
            raise StopRejectedError("state_conflict", "member is dead or permanently exiting")

    async def wake_member(self, member_id: str) -> bool:
        """Cancel sleep and check actual mail, without inventing new work."""
        lock = self._wake_locks.setdefault(member_id, asyncio.Lock())
        async with lock:
            await self._require_live_member(member_id)
            intent = self._scheduling_intents.get(member_id)
            agent = self._agents.get(member_id)
            if (
                intent is None
                or intent[0] != "sleep"
                or (agent is not None and agent.active_session_count > 0)
            ):
                raise StopRejectedError("state_conflict", "member is not sleeping")
            await asyncio.to_thread(self._scheduling_store.delete, member_id)
            self._scheduling_intents.pop(member_id, None)
            timer = self._sleep_tasks.pop(member_id, None)
            if timer is not None:
                timer.cancel()
        await self.publish_scheduling_state(member_id)
        await self._resume_unread_mail(member_id, "admin_wake")
        return True

    async def wait_member(self, member_id: str) -> bool:
        """Drain current work, then restore normal mail scheduling."""
        return await self._admin_scheduling_request(member_id, "wait", None)

    async def sleep_member(self, member_id: str, wake_at: str) -> bool:
        """Drain current work and sleep until a future timezone-aware deadline."""
        deadline = datetime.fromisoformat(wake_at)
        if deadline.tzinfo is None or deadline <= datetime.now(UTC):
            raise ValueError("wake_at must be a future time with timezone")
        return await self._admin_scheduling_request(
            member_id, "sleep", deadline.astimezone(UTC).isoformat()
        )

    async def _admin_scheduling_request(
        self, member_id: str, action: str, wake_at: str | None
    ) -> bool:
        lock = self._wake_locks.setdefault(member_id, asyncio.Lock())
        async with lock:
            await self._require_live_member(member_id)
            old = self._scheduling_intents.get(member_id)
            agent = self._agents.get(member_id)
            busy = agent is not None and agent.active_session_count > 0
            if old is not None and (action == "wait" or busy or old[0] != "sleep"):
                raise StopRejectedError("state_conflict", "another scheduling request is pending")
            phase = "pending" if busy else "committed"
            intent = (action, wake_at, f"admin:{phase}:{uuid4().hex}")
            self._scheduling_intents[member_id] = intent
            try:
                await asyncio.to_thread(self._scheduling_store.put, member_id, *intent)
            except Exception:
                if old is None:
                    self._scheduling_intents.pop(member_id, None)
                else:
                    self._scheduling_intents[member_id] = old
                raise
            timer = self._sleep_tasks.pop(member_id, None)
            if timer is not None:
                timer.cancel()
        await self.publish_scheduling_state(member_id)
        if busy:
            self._launch_lifecycle(self._drain_scheduling_request(member_id, intent))
            return False
        await self._drain_scheduling_request(member_id, intent)
        return True

    async def prepare_scheduling_request(
        self, member_id: str, request: dict[str, Any], session_id: str
    ) -> None:
        """Commit a self intention without overriding administrator drain requests."""
        action = request["action"]
        if action == "finish":
            return
        if action not in {"sleep", "exit"}:
            raise ValueError("unknown scheduling action")
        wake_at = None
        if action == "sleep":
            deadline = datetime.fromisoformat(request["wake_at"])
            if deadline.tzinfo is None:
                raise ValueError("wake_at must include timezone")
            wake_at = deadline.astimezone(UTC).isoformat()
        lock = self._wake_locks.setdefault(member_id, asyncio.Lock())
        async with lock:
            old = self._scheduling_intents.get(member_id)
            if member_id in self._dead or member_id in self._pending_stops:
                return
            if old is not None and (
                old[0] == "exit" or (action != "exit" and old[2].startswith("admin:"))
            ):
                return
            self._scheduling_intents[member_id] = (action, wake_at, session_id)
            try:
                await asyncio.to_thread(
                    self._scheduling_store.put, member_id, action, wake_at, session_id
                )
            except Exception:
                if old is None:
                    self._scheduling_intents.pop(member_id, None)
                else:
                    self._scheduling_intents[member_id] = old
                raise

    async def apply_scheduling_request(self, member_id: str, request: dict[str, Any]) -> None:
        """Reap released its slot; independently drain without holding a wake lock."""
        if request["action"] == "finish":
            return
        if member_id not in self._scheduling_intents:
            await self.prepare_scheduling_request(member_id, request, "")
        intent = self._scheduling_intents.get(member_id)
        if intent is None:
            return
        if intent[0] == "exit":
            self._launch_lifecycle(
                self.stop_member(member_id, stopped_by=member_id, when_idle=True)
            )
        else:
            self._launch_lifecycle(self._drain_scheduling_request(member_id, intent))

    async def _drain_scheduling_request(
        self, member_id: str, expected: tuple[str, str | None, str]
    ) -> None:
        lock = self._wake_locks.setdefault(member_id, asyncio.Lock())
        while True:
            async with lock:
                if (
                    self._scheduling_intents.get(member_id) != expected
                    or member_id in self._dead
                    or member_id in self._pending_stops
                ):
                    return
                agent = self._agents.get(member_id)
                if agent is None or agent.active_session_count == 0:
                    if agent is not None:
                        await agent.shutdown()
                        self._agents.pop(member_id, None)
                    if expected[0] == "wait":
                        await asyncio.to_thread(self._scheduling_store.delete, member_id)
                        self._scheduling_intents.pop(member_id, None)
                    else:
                        assert expected[1] is not None
                        if expected[2].startswith("admin:pending:"):
                            committed = (
                                expected[0],
                                expected[1],
                                expected[2].replace("admin:pending:", "admin:committed:", 1),
                            )
                            await asyncio.to_thread(
                                self._scheduling_store.put, member_id, *committed
                            )
                            self._scheduling_intents[member_id] = committed
                        self._schedule_deadline(member_id, expected[1])
                    break
            # A completing session may need this same lock to commit self intent.
            await asyncio.sleep(0.05)
        await self.publish_scheduling_state(member_id)
        if expected[0] == "wait":
            await self._resume_unread_mail(member_id, "admin_wait")

    def _schedule_deadline(self, member_id: str, wake_at: str) -> None:
        previous = self._sleep_tasks.get(member_id)
        if previous is not None:
            previous.cancel()
        intent = self._scheduling_intents[member_id]
        self._sleep_tasks[member_id] = asyncio.create_task(
            self._wake_at_deadline(member_id, wake_at, intent)
        )

    async def _wake_at_deadline(
        self, member_id: str, wake_at: str, expected: tuple[str, str | None, str]
    ) -> None:
        try:
            delay = (datetime.fromisoformat(wake_at) - datetime.now(UTC)).total_seconds()
            await asyncio.sleep(max(0.0, delay))
            lock = self._wake_locks.setdefault(member_id, asyncio.Lock())
            async with lock:
                if (
                    self._scheduling_intents.get(member_id) != expected
                    or member_id in self._dead
                    or member_id in self._pending_stops
                ):
                    return
                await asyncio.to_thread(self._scheduling_store.delete, member_id)
                self._scheduling_intents.pop(member_id, None)
            await self.publish_scheduling_state(member_id)
            await self._resume_unread_mail(member_id, "sleep_timer")
        finally:
            if self._sleep_tasks.get(member_id) is asyncio.current_task():
                self._sleep_tasks.pop(member_id, None)

    async def _resume_unread_mail(self, member_id: str, source: str) -> None:
        store = MailStore(self._paths.mail_database_file())
        if member_id in await asyncio.to_thread(store.unread_member_ids):
            await self._on_mail_delivered(
                Event(
                    type=EventType.MAIL_DELIVERED,
                    payload={"recipient": member_id},
                    source=source,
                )
            )

    async def get_agent_status(self, agent_id: str) -> dict[str, Any] | None:
        """Get status of a specific agent.

        Args:
            agent_id: Agent ID

        Returns:
            Agent status dictionary or None if not found
        """
        config = self._agent_info.get_config(agent_id)
        if not config:
            return None

        retired = await asyncio.to_thread(
            MailStore(self._paths.mail_database_file()).is_retired, agent_id
        )
        agent = self._agents.get(agent_id)
        if retired:
            state = "dead"
            self._dead.add(agent_id)
        elif agent is not None and agent.active_session_count > 0:
            state = "active"
        elif (
            agent_id in self._scheduling_intents
            and self._scheduling_intents[agent_id][0] == "sleep"
        ):
            state = "sleeping"
        elif agent:
            state = agent.get_state()
        elif agent_id in self._waking:
            state = "waiting"
        else:
            state = "waiting"

        return {
            "id": agent_id,
            "name": config.name,
            "state": state,
            "resident": agent is not None,
            "waking": agent_id in self._waking,
            "error": agent.get_diagnostic_error() if isinstance(agent, MemberRuntime) else None,
            "scheduling_pending": self._scheduling_intents.get(agent_id, (None, None, ""))[0],
            "stop_pending": agent_id in self._pending_stops,
            "wake_at": self._scheduling_intents.get(agent_id, ("", None, ""))[1]
            if state == "sleeping"
            else None,
            "prototype": config.prototype,
            "spawned_by": config.spawned_by,
            "spawn_depth": config.spawn_depth,
        }

    async def get_all_agent_status(self) -> list[dict[str, Any]]:
        """Get status of all registered agents.

        Returns:
            List of agent status dictionaries
        """
        return [
            status
            for agent_id in self._agent_info.get_all_ids()
            if (status := await self.get_agent_status(agent_id)) is not None
        ]
