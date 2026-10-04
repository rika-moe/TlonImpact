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
  never discarded — they sleep
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
*derives* it from each member's spec — a sleeping recipient whose spec
declares ``:on :mail-delivered`` is woken. Lifecycle membership is
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
from contextlib import suppress
from typing import TYPE_CHECKING, Any

from tlon.agents.assembly import assemble_cognitive
from tlon.agents.cron import CronScheduler
from tlon.agents.health import PublicHealthMonitor
from tlon.agents.lifecycle import StartRequest, StopRequest
from tlon.agents.loader import load_member_spec
from tlon.agents.member_runtime import MemberRuntime
from tlon.agents.member_spec import MemberSpec
from tlon.agents.policy import (
    policy_probation_provider,
    policy_solvency_provider,
    policy_tilt_provider,
)
from tlon.agents.prototypes.catalog import get_prototype_info
from tlon.agents.reaper import IdleReaper
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
from tlon.organization.mail.mailbox import Mailbox
from tlon.tools.agents.ports import StopRejectedError

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
        return (
            self._registry.get_agent(agent_id) is None
            and self._registry.get_config(agent_id) is not None
        )

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
        """The social roster (ADR 0003): one line per registered member.

        Format: ``名字（id） — prototype：描述``; the prototype segment is
        omitted for manual members, the description when the prototype is
        absent from the library. Sleeping members stay on the roster — it
        answers "who is in this society", not "who is awake". Adam is not
        registered, so it never lists itself.
        """
        lines: list[str] = []
        for agent_id in self._registry.get_all_ids():
            config = self._registry.get_config(agent_id)
            if config is None:
                continue
            line = f"{config.name}（{agent_id}）"
            if config.prototype:
                line += f" — {config.prototype}"
                info = get_prototype_info(config.prototype, self._registry._paths)
                if info is not None and info.description:
                    line += f"：{info.description}"
            lines.append(line)
        return "\n".join(lines)


class AgentRegistry(Component):
    """Registry for managing multiple agents — pure mechanism.

    Activation-aware run state (see docs/design/members.md, 阶段 D):
    - The registry owns the run table, per-agent wake locks, and the
      ``_waking`` dedup set, and executes AGENT_START_REQUEST /
      AGENT_STOP_REQUEST idempotently.
    - "lazy" agents sleep until a start request arrives and are drained
      after `idle_timeout_seconds` of inactivity with no live sessions.
      Unified member model (ADR 0006): draining always means sleep —
      members are never discarded, home directories are archived forever.
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
                skips the getMetrics tool
            trace_store: Trace pillar injected into each member's
                observability tools (read-only); None skips the
                listTraces/getTrace tools
        """
        super().__init__(name="AgentRegistry")
        self.event_bus = event_bus
        self._agent_info = agent_info_provider
        self._paths = tlon_paths
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
            event_bus=event_bus,
            agent_info_provider=agent_info_provider,
            tlon_paths=tlon_paths,
        )

        # Registered agent IDs (persona full set; may or may not be running)
        self._registered_ids: list[str] = []

        # Running agents (activations): one unified member runtime — all
        # expose shutdown / get_state / active_session_count / idle_seconds
        self._agents: dict[str, MemberRuntime] = {}

        # Docker runtimes are built during cognitive assembly, keyed by
        # agent id — but the registry deliberately does NOT hold handles
        # for reclaim: a sleeping member keeps its container (zero-cost
        # wake / lazy reuse on next boot, unified member model — members
        # never have their home or container force-removed, ADR 0006).
        # A re-assembly reuses the container by name.

        # Request execution state: agents currently being started, and one
        # lock per agent serializing start/drain of the same persona
        self._waking: set[str] = set()
        self._wake_locks: dict[str, asyncio.Lock] = {}

        # Pending stops (stopAgent when_idle=true): agent_id → stopped_by,
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
            spec = load_member_spec(agent_id, tlon_paths=self._paths, builtins=self._host_builtins)
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
        logger.info("agent_registry_shutdown_started")

        # Stop the idle monitor first so it cannot drain mid-shutdown
        if self._idle_monitor_task:
            self._idle_monitor_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._idle_monitor_task
            self._idle_monitor_task = None

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
        Every member gets the full capability set (``assemble_cognitive`` in
        ``agents/assembly.py``: LLM client, memory, task manager, Docker
        runtime, tools); whether the cognitive engine (scheduler / loop) is
        attached is derived from the spec — a rule with a ``:loop`` gets
        it, one without does not (lazy assembly: objects are cheap,
        loading is on demand).

        Args:
            agent_id: ID of the agent to start

        Returns:
            The started MemberRuntime instance, or None if not found
        """
        if agent_id in self._agents:
            # Already active
            return self._agents[agent_id]

        config = self._agent_info.get_config(agent_id)
        if not config:
            logger.error("unknown_agent", agent_id=agent_id)
            return None

        try:
            logger.info("agent_starting", agent_id=agent_id, agent_name=config.name)

            # Unified assembly (ADR 0006): every member gets the full
            # capability set; the cognitive engine attachment is derived
            # from the spec inside MemberRuntime. Host builtins gate
            # validation too (D6.6: host predicates open to registry
            # members).
            builtins = self._host_builtins
            spec = load_member_spec(agent_id, tlon_paths=self._paths, builtins=builtins)
            self._profiles[agent_id] = spec  # cache the profile (phase E)
            mailbox = Mailbox(self._paths.member_mail_dir(config.id))
            capabilities = await assemble_cognitive(
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
                builtins=builtins,
                mailbox=mailbox,
                cognitive=capabilities,
                registry=self,
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

        except Exception as e:
            logger.error("agent_start_failed", agent_id=agent_id, error=str(e), exc_info=True)
            return None

    # ==================== Lifecycle request execution ====================

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

    async def _on_start_request(self, event: Event) -> None:
        """Execute an AGENT_START_REQUEST decision (mechanism only).

        Runs in the EventBus dispatch context, so it must stay fast: the
        actual assembly happens in a background task. Idempotent — a
        running or already-waking agent, or an unknown id, is skipped.
        The dedup state is updated synchronously here so back-to-back
        requests for the same persona collapse to one assembly.
        """
        request = event.payload
        if not isinstance(request, StartRequest):
            return

        agent_id = request.agent_id
        if agent_id in self._agents or agent_id in self._waking:
            return
        if not self._agent_info.get_config(agent_id):
            return
        self._waking.add(agent_id)
        asyncio.create_task(self._fulfill_start_request(request, event))

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
        asyncio.create_task(self._reaper.drain(request.agent_id, request.reason))

    # ==================== Hot adoption + spawn (stage 6b) ====================

    async def adopt_agent(self, agent_id: str) -> str:
        """Adopt an agent created out-of-process (wizard/mirror), no restart.

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
        """Stop a member: drain-check, then put it to sleep.

        Unified member model (ADR 0006): stopping never deletes — a member
        sleeps (scale-to-zero) and its home directory is archived forever.
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
            True when the member was stopped (asleep); False when a
            pending stop was registered instead (``when_idle=True`` only).

        Raises:
            StopRejectedError: unknown agent / live sessions without
                ``when_idle``
        """
        config = self._agent_info.get_config(agent_id)
        if config is None:
            raise StopRejectedError("unknown_agent", f"agent '{agent_id}' not found")

        agent = self._agents.get(agent_id)
        if agent is not None and agent.active_session_count > 0:
            if not when_idle:
                raise StopRejectedError(
                    "active_sessions",
                    f"agent '{agent_id}' has {agent.active_session_count} live session(s); "
                    "wait for them to finish (drain), or stop with when_idle=true",
                )
            self._register_pending_stop(agent_id, stopped_by)
            return False

        # Serialize with a concurrent wake of the same persona
        lock = self._wake_locks.setdefault(agent_id, asyncio.Lock())
        async with lock:
            agent = self._agents.pop(agent_id, None)
            if agent is not None:
                # Drain re-check under the lock: a session that started in
                # the race window keeps the member awake
                if agent.active_session_count > 0:
                    self._agents[agent_id] = agent
                    if when_idle:
                        self._register_pending_stop(agent_id, stopped_by)
                        return False
                    raise StopRejectedError(
                        "active_sessions",
                        f"agent '{agent_id}' gained a live session; stop aborted",
                    )
                try:
                    await agent.shutdown()
                except Exception as e:
                    # Already out of the run table; a later start request
                    # assembles a fresh activation.
                    logger.error("agent_stop_failed", agent_id=agent_id, error=str(e))

            await self.event_bus.publish(
                Event(
                    type=EventType.AGENT_STOPPED,
                    payload={
                        "agent_id": agent_id,
                        "stopped_by": stopped_by,
                    },
                    source="agent_registry",
                )
            )

        # The stop fulfills any pending intent for this member (cancel the
        # watcher outside the wake lock; never cancel ourselves — the
        # watcher calls stop_member to fire its intent).
        self._pending_stops.pop(agent_id, None)
        watcher = self._pending_stop_tasks.pop(agent_id, None)
        if watcher is not None and watcher is not asyncio.current_task():
            watcher.cancel()
            with suppress(asyncio.CancelledError):
                await watcher

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
        self._pending_stop_tasks[agent_id] = asyncio.create_task(
            self._watch_pending_stop(agent_id)
        )

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
                    # Already asleep (drained or stopped): intent fulfilled.
                    self._pending_stops.pop(agent_id, None)
                    return
                if agent.active_session_count > 0:
                    continue
                self._pending_stops.pop(agent_id, None)
                try:
                    stopped_now = await self.stop_member(
                        agent_id, stopped_by=stopped_by, when_idle=True
                    )
                except StopRejectedError as e:
                    # Only unknown_agent can reach here (the member left
                    # the config); the intent is unfulfillable.
                    logger.warning(
                        "pending_stop_dropped", agent_id=agent_id, reason=e.reason
                    )
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

        Distinct from ``stop_member`` (which sleeps, keeping the home
        archive): this deletes the member's records — it is stopped
        (sleeps) if running, unregistered, and its home directory is
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
                except Exception as e:
                    logger.error("agent_stop_failed", agent_id=agent_id, error=str(e))

            if agent_id in self._registered_ids:
                self._registered_ids.remove(agent_id)
            self._profiles.pop(agent_id, None)
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

    def get_agent_status(self, agent_id: str) -> dict[str, Any] | None:
        """Get status of a specific agent.

        Args:
            agent_id: Agent ID

        Returns:
            Agent status dictionary or None if not found
        """
        config = self._agent_info.get_config(agent_id)
        if not config:
            return None

        agent = self._agents.get(agent_id)

        if agent:
            state = agent.get_state()
        elif agent_id in self._waking:
            state = "waking"
        elif self.is_lazy(agent_id):
            state = "sleeping"
        else:
            state = "unknown"

        return {
            "id": agent_id,
            "name": config.name,
            "state": state,
            "prototype": config.prototype,
            "spawned_by": config.spawned_by,
            "spawn_depth": config.spawn_depth,
        }

    def get_all_agent_status(self) -> list[dict[str, Any]]:
        """Get status of all registered agents.

        Returns:
            List of agent status dictionaries
        """
        return [
            status
            for agent_id in self._agent_info.get_all_ids()
            if (status := self.get_agent_status(agent_id)) is not None
        ]
