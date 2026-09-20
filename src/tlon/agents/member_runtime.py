"""MemberRuntime — the unified member interpreter (phase C).

Phase A built the :class:`~tlon.agents.member_spec.MemberSpec`; phase B made
it the single spec flowing through the system; phase C collapses the two
runtimes (the cognitive ``Agent`` / LoopDriver engine and the deterministic
``ScriptMember`` shell) into one interpreter whose behavior is entirely
described by a member's spec (``:on`` + ``:when`` + optional ``:loop`` +
``:actions`` — a hit runs the loop first, then the actions; no loop, actions
only).

The runtime is the ScriptMember outer shell generalized: per-rule ``:on``
subscriptions (deduped), ``:boot`` rules, the unread-mail rescue scan, the
own-mail guard, and read-marking — plus, when the registry assembles
:class:`CognitiveCapabilities`, the cognitive engine (LoopDriver +
SessionScheduler + context builder + reflection + on-complete runner)
attached to the member's one cognitive rule. The two former runtimes' envs
unify to the superset ``{msg, event, self}`` (completion adds ``result``),
and all five action kinds (``spawn`` / ``emit`` / ``start`` / ``stop`` /
``forward``) execute through one executor.

Derived values: ``state`` / ``active_session_count`` /
``idle_seconds`` delegate to the scheduler when cognitive, else to
deterministic idle bookkeeping — with real background activity (an
in-flight reflection) folded into ``idle_seconds`` so a member mid-audit
is never reported idle.

Mail semantics: a delivered mail addressed to this member is marked read
once processed — a deterministic hit reads it immediately; a cognitive hit
defers the read to session completion; a mail the ``:when`` gate rejects is
read anyway (D6.1, retiring the react "never responds → mail stuck unread"
quirk). A mail that a full bounded queue drops stays unread for the
rescan — the member's one background rescan task picks it up on its
periodic sweep, without waiting for a reboot (a boot member with
``:on :boot`` never gets a second boot scan). Own mail (sent by this
member) is never processed.

Boot-scan throttling (issue ): the unread rescue scan merges one
thread into one session (the thread's latest unread mail is the
representative; earlier siblings are marked read as covered — the
session's mail-history context renders the whole thread), and overflow
the bounded queue cannot take stays fully unread (deterministic rules
deferred with it) for the same rescan task, which trickles mail in as
the scheduler drains — backlog is queued, never dropped on the floor.
"""

import asyncio
import contextlib
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

from structlog.contextvars import bound_contextvars, get_contextvars

from tlon.agents.context_builder import ContextBuilder
from tlon.agents.context_pressure import ContextPressure
from tlon.agents.executor import EffectExecutor
from tlon.agents.lifecycle import StartRequest, StopRequest
from tlon.agents.loop_driver import LoopDriver
from tlon.agents.member_spec import MemberRule, MemberSpec
from tlon.agents.on_complete import OnCompleteRunner
from tlon.agents.reflection import ReflectionEngine
from tlon.agents.scheduler import SessionScheduler
from tlon.agents.session_journal import SessionJournal
from tlon.agents.stages.services import StageServices, make_event_publisher
from tlon.agents.tool_executor import ToolExecutor
from tlon.agents.trigger import AgentIdentity
from tlon.base.component import Component
from tlon.base.config import DEFAULT_CONTEXT_WINDOW, AgentConfig
from tlon.base.device import Device
from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus
from tlon.base.llm_client import LLMClient
from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths
from tlon.base.tracing.trace_context import get_current_trace
from tlon.memory.manager import MemoryManager
from tlon.organization.mail.mail import Mail, address_for_member
from tlon.organization.mail.mailbox import Mailbox
from tlon.organization.messaging.member import Member
from tlon.tools.spill import ToolSpill
from tlon.tools.tasks.manager import TaskManager
from tlosure.builtins import ActionRequest, Builtin, EvalError, FuelExhaustedError
from tlosure.eval import (
    DEFAULT_FUEL,
    DEFAULT_MAX_DEPTH,
    DEFAULT_WHEN_FUEL,
    _Evaluator,
    eval_form,
)

if TYPE_CHECKING:
    from tlon.agents.agent_registry import AgentRegistry

logger = get_logger(__name__)

#: Trace carrier triple for forwarding an event's span tree to a decision.
Carrier = tuple[str | None, str | None, str | None]

#: Poll interval of the deferred rescan that trickles boot-scan overflow
#: into the scheduler as sessions drain (issue ).
_RESCAN_POLL_SECONDS = 5.0


class MemberState(Enum):
    """Member lifecycle states (cognitive members; deterministic ones are
    simply active/offline)."""

    INITIALIZING = "initializing"  # Starting up
    ACTIVE = "active"  # Running and processing messages
    THINKING = "thinking"  # Currently processing a message
    ERROR = "error"  # Encountered an error
    SHUTDOWN = "shutdown"  # Stopped


@dataclass
class CognitiveCapabilities:
    """The full capability set every member gets (unified member model,
    ADR 0006): LLM client, memory, task manager, Docker runtime, and
    tools.

    Assembly is unconditional (lazy assembly: objects are cheap, loading
    is on demand); whether the cognitive *engine* (scheduler / loop) is
    attached is derived from the spec inside ``MemberRuntime`` — a member
    without a loop rule simply never uses these objects. ``None`` is only
    used by built-in instances like Adam (no capabilities at all).
    """

    config: AgentConfig
    llm_client: LLMClient
    memory: MemoryManager
    task_manager: TaskManager
    tool_executor: ToolExecutor
    team_members: list[AgentConfig]
    tlon_paths: TlonPaths
    per_tool_timeout: float = 30.0
    registry: "AgentRegistry | None" = None
    # Trust ladder (): a probationary member's mentor address (its
    # spawner), cc'd on composed outbound mail; None for full members.
    mentor_cc: str | None = None


class MemberRuntime(Member):
    """Unified member interpreter: spec-driven, cognitive or deterministic.

    All work happens in the EventBus dispatch context (no worker task; the
    bus already isolates handler exceptions). Cognitive work is enqueued as
    sessions and drained by the scheduler's main loop task. Decisions are
    request events on the bus; execution is the registry's job.
    """

    def __init__(
        self,
        *,
        member_id: str,
        name: str,
        event_bus: EventBus,
        spec: MemberSpec,
        builtins: dict[str, Builtin],
        mailbox: Mailbox | None = None,
        cognitive: CognitiveCapabilities | None = None,
        registry: "AgentRegistry | None" = None,
    ):
        """Initialize the member runtime.

        Args:
            member_id: Member identity on the bus
            name: Display name (component/device name)
            event_bus: Event bus for subscriptions and request publishing
            spec: The policy spec (pure data, validated at parse time)
            builtins: Evaluation registry, host predicate closures
                included (built by the caller via make_member_builtins)
            mailbox: The member's mailbox for unread rescue scans and
                read-marking (None only for bare test instances; Adam
                carries ``~/.tlon/adam/mail/`` since issue )
            cognitive: The member's capability set (LLM/memory/tools).
                The registry assembles it for every member; None is only
                used by built-in instances like Adam. The cognitive
                engine (scheduler / loop) is attached iff the spec has a
                loop rule — derived, never stored.
            registry: Agent registry for ``spawn`` actions (None, e.g.
                Adam, disables them)
        """
        Component.__init__(self, name=name)
        Device.__init__(self, event_bus=event_bus, name=name)
        self._member_id = member_id
        self._spec = spec
        self._builtins = builtins
        self._mailbox = mailbox
        self._cognitive = cognitive
        self._registry = (
            registry if registry is not None else (cognitive.registry if cognitive else None)
        )
        # Trust ladder (): the probation mentor's address, cc'd on
        # composed outbound mail (_send_mail; the cognitive path's
        # EffectExecutor gets it in _assemble_cognitive). Forwarding is
        # not cc'd — passing along an existing mail is noise to the mentor.
        self._mentor_cc = cognitive.mentor_cc if cognitive is not None else None
        # The one cognitive rule, if any (D6.7: at most one per member).
        self._cognitive_rule = next(
            (rule for rule in self._spec.rules if rule.loop is not None), None
        )
        # Deterministic idle bookkeeping (registry drain): initialize time
        # and the last executed rule action, both monotonic.
        self._initialized_at: float | None = None
        self._last_action_at: float | None = None
        # Cognitive subsystem slots; None for deterministic members.
        self._scheduler: SessionScheduler | None = None
        self._loop_driver: LoopDriver | None = None
        self._effect_executor: EffectExecutor | None = None
        self._stage_services: StageServices | None = None
        self._reflection: ReflectionEngine | None = None
        self._context_builder: ContextBuilder | None = None
        self._logger = logger.bind(agent_id=member_id)
        self._state: MemberState | None = None
        # Boot-scan throttle state (issue ): mails a scan enqueued
        # whose sessions have not yet marked them read, whether the last
        # scan left overflow unread (bounded queue full), and the one
        # rescan task that both trickles overflow in as the scheduler
        # drains and periodically re-scans unread mail.
        self._scan_pending: set[str] = set()
        self._scan_deferred = False
        self._rescan_task: asyncio.Task[None] | None = None
        # Cognitive engine attachment is spec-derived (ADR 0006): a member
        # with a loop rule gets the engine, one without keeps the full
        # capability set unused (lazy assembly — objects are cheap,
        # loading is on demand).
        if cognitive is not None and self._cognitive_rule is not None:
            self._assemble_cognitive(cognitive)
        with bound_contextvars(agent_id=self._member_id):
            logger.info(
                "member_initialized",
                member_id=member_id,
                cognitive_engine=self._cognitive_rule is not None,
            )

    # ── Identity / derived values ──

    @property
    def member_id(self) -> str:
        """Member identity on the bus."""
        return self._member_id

    @property
    def spec(self) -> MemberSpec:
        """The member's behavior spec (the semantic authority)."""
        return self._spec

    def get_state(self) -> str:
        """Member state: cognitive derives ACTIVE/THINKING from live
        sessions on read; deterministic is simply active once initialized."""
        if self._scheduler is not None:
            if self._state in (MemberState.ACTIVE, MemberState.THINKING):
                if self._scheduler.active_session_count > 0:
                    return MemberState.THINKING.value
                return MemberState.ACTIVE.value
            return self._state.value if self._state else "unknown"
        return "active" if self._initialized else "offline"

    @property
    def active_session_count(self) -> int:
        """Live sessions (cognitive) or 0 (deterministic); the registry's
        idle monitor and drain decisions read this."""
        if self._scheduler is not None:
            return self._scheduler.active_session_count
        return 0

    @property
    def idle_seconds(self) -> float:
        """Seconds since the last activity.

        Cognitive: the scheduler's session activity clock, with real
        background activity folded in — an in-flight reflection LLM call
        is work in progress and counts as active, so the member is not
        reported idle and drained mid-audit. The rescan task's mere
        existence does not count: it sleeps between sweeps, and a
        sweep's enqueued mail already becomes session activity.
        Deterministic: time since the last executed rule action (from
        initialize when no action has run yet); 0 before initialization.
        """
        if self._scheduler is not None:
            if self._reflection is not None and self._reflection.is_running:
                return 0.0
            return self._scheduler.idle_seconds
        if self._initialized_at is None:
            return 0.0
        since = (
            self._last_action_at
            if self._last_action_at is not None
            else self._initialized_at
        )
        return time.monotonic() - since

    # ── Lifecycle ──

    async def initialize(self) -> None:
        """Subscribe the rules' event types (deduped), run boot rules, the
        unread-mail rescue scan, and (cognitive) load memory and start the
        scheduler's main loop.

        Boot rules run before the scan; requests are published and
        initialize() returns without waiting for their recipients — the
        registry executes them in background tasks. The unread scan replays
        mail that arrived while the member slept (the mailbox is the durable
        queue).
        """
        with bound_contextvars(agent_id=self._member_id):
            if self._cognitive_rule is not None:
                assert self._cognitive is not None
                await self._cognitive.memory.load()
                self._cognitive.task_manager.load()
                self._state = MemberState.INITIALIZING

            event_types = {rule.on for rule in self._spec.rules if rule.on is not None}
            for event_type in sorted(event_types, key=str):
                self.subscribe(event_type, self._on_event)

            boot_rules = [rule for rule in self._spec.rules if rule.on is None]
            for rule in boot_rules:
                await self._run_rule(rule, trigger_event=None)

            self._initialized = True
            self._initialized_at = time.monotonic()

            await self._scan_unread_mail()

            if self._cognitive_rule is not None:
                assert self._scheduler is not None
                self._scheduler.start()
                self._state = MemberState.ACTIVE
                # One rescan task per member (issue ): it trickles
                # boot-scan overflow in as the scheduler drains and, on a
                # longer cadence, re-scans unread mail so live mail the
                # full queue dropped is picked up while the member runs —
                # a boot member never reboots, so there is no "next
                # boot scan" for it.
                self._rescan_task = asyncio.create_task(self._rescan_loop())

            logger.info(
                "member_runtime_initialized",
                member_id=self._member_id,
                subscriptions=sorted(event_type.value for event_type in event_types),
                boot_rules=[rule.name for rule in boot_rules],
            )

    async def shutdown(self) -> None:
        """Shutdown the member: cancel the background tasks (rescan,
        in-flight reflection), stop the cognitive loop, release
        subscriptions, shut down tools (cancelling background bash tasks)."""
        with bound_contextvars(agent_id=self._member_id):
            if self._rescan_task is not None:
                self._rescan_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._rescan_task
                self._rescan_task = None
            if self._scheduler is not None:
                await self._scheduler.stop()
            if self._reflection is not None:
                # The scheduler is stopped, so no new reflection can
                # trigger; cancel one already in flight — no
                # free-floating task survives the member.
                await self._reflection.shutdown()
            self.unsubscribe_all()
            if self._cognitive is not None:
                await self._cognitive.tool_executor.shutdown()
            if self._scheduler is not None:
                self._state = MemberState.SHUTDOWN
            self._initialized = False
            self._initialized_at = None
            logger.info("member_runtime_shutdown", member_id=self._member_id)

    # ── Event dispatch ──

    async def _on_event(self, event: Event) -> None:
        """Dispatch one bus event to every rule subscribed to its type.

        A cognitive rule on MAIL_DELIVERED gates and enqueues a session (the
        read is deferred to session completion); deterministic rules
        evaluate and execute their actions inline. Mail sent by the member
        itself is ignored (own-mail guard) and marked read immediately —
        an ``all@`` self-copy must not linger as unread until the next
        boot scan. A delivered mail not enqueued for a cognitive session
        is marked read after processing (D6.1).
        """
        mail = event.payload.get("mail") if isinstance(event.payload, dict) else None
        is_own_mail = (
            event.type is EventType.MAIL_DELIVERED
            and isinstance(mail, Mail)
            and mail.from_address == self.address
        )
        if is_own_mail:
            await self._mark_read(mail)
            return
        delivered_to_me = (
            event.type is EventType.MAIL_DELIVERED
            and isinstance(mail, Mail)
            and event.payload.get("recipient") == self._member_id
        )

        cognitive_deferred = False
        dropped = False
        for rule in self._spec.rules:
            if rule.on is None or rule.on != event.type:
                continue
            if rule.loop is not None:
                if event.type is not EventType.MAIL_DELIVERED:
                    logger.warning(
                        "cognitive_rule_non_mail_event",
                        member_id=self._member_id,
                        rule=rule.name,
                        event_name=event.type.value,
                    )
                    continue
                if delivered_to_me:
                    assert isinstance(mail, Mail)
                    result = await self._enqueue_cognitive(rule, mail, event)
                    if result == "queued":
                        cognitive_deferred = True
                    elif result == "dropped":
                        dropped = True
            else:
                await self._run_rule(rule, trigger_event=event)

        if delivered_to_me and not cognitive_deferred and not dropped:
            await self._mark_read(mail)

    async def _enqueue_cognitive(
        self, rule: MemberRule, mail: Mail, trigger_event: Event | None
    ) -> str:
        """Gate a delivered mail and enqueue a session for it.

        Returns "queued" | "rejected" | "dropped". "rejected" (the ``:when``
        gate failed, or the mail is not addressed to this member) marks the
        mail read now (D6.1); "dropped" (bounded queue full) leaves it
        unread for the rescan task's next sweep.
        """
        if not self._should_respond(rule, mail):
            return "rejected"
        carrier = {
            "trace_id": trigger_event.trace_id if trigger_event else None,
            "span_id": trigger_event.span_id if trigger_event else None,
        }
        if self._scheduler is None or not self._scheduler.enqueue(mail, carrier):
            logger.warning(
                "mail_dropped",
                member_id=self._member_id,
                sender=mail.from_address,
                mail_id=mail.id,
            )
            return "dropped"
        logger.debug("mail_queued", sender=mail.from_address, mail_id=mail.id)
        return "queued"

    @staticmethod
    def _rule_fuel(rule: MemberRule, default: int) -> int:
        """A rule's evaluation budget: ``:fuel`` when declared, else ``default``."""
        return rule.fuel if rule.fuel is not None else default

    def _should_respond(self, rule: MemberRule, mail: Mail) -> bool:
        """Run the cognitive rule's ``:when`` gate (own-mail already checked
        by the caller); a None :when is ungated — always respond. A gate
        evaluation failure safely defaults to no response.

        The gate runs under the rule's ``:fuel`` when declared (phase D),
        else the independent small :data:`DEFAULT_WHEN_FUEL`. Fuel
        exhaustion is logged distinctly from a plain gate failure
        (dsl-agents.md phase D).
        """
        if rule.when is None:
            return True
        env = self._env({"mail": mail, "recipient": self._member_id})
        try:
            return bool(
                eval_form(
                    rule.when,
                    env,
                    self._builtins,
                    fuel=self._rule_fuel(rule, DEFAULT_WHEN_FUEL),
                )
            )
        except FuelExhaustedError as e:
            logger.warning(
                "member_rule_gate_fuel_exhausted",
                member_id=self._member_id,
                rule=rule.name,
                error=str(e),
            )
            return False
        except EvalError as e:
            logger.warning(
                "member_rule_gate_failed",
                member_id=self._member_id,
                rule=rule.name,
                error=str(e),
            )
            return False

    # ── Deterministic rule evaluation ──

    async def _run_rule(
        self,
        rule: MemberRule,
        trigger_event: Event | None,
        payload: Any = None,
    ) -> None:
        """Evaluate one rule against an event and execute its decisions.

        ``payload`` overrides the environment's ``event`` value; it is used
        by the unread rescue scan, which has no triggering bus event. An
        EvalError (gate or action evaluation) skips the rule with a warning
        — other rules and the bus dispatch are never affected. Fuel
        exhaustion during the rule's evaluation is logged distinctly from a
        plain evaluation failure (dsl-agents.md phase D).
        """
        if trigger_event is not None:
            payload = trigger_event.payload
        env = self._env(payload)
        try:
            requests = self._evaluate_rule(rule, env)
        except FuelExhaustedError as e:
            logger.warning(
                "member_rule_fuel_exhausted",
                member_id=self._member_id,
                rule=rule.name,
                error=str(e),
            )
            return
        except EvalError as e:
            logger.warning(
                "member_rule_eval_failed",
                member_id=self._member_id,
                rule=rule.name,
                error=str(e),
            )
            return
        logger.debug(
            "member_rule_matched",
            member_id=self._member_id,
            rule=rule.name,
            action_count=len(requests),
        )
        # The triggering mail doubles as the session mail so a deterministic
        # ``emit`` with empty recipients replies to the sender (phase F: the
        # reply-to-sender semantics of ``_execute_emit`` now holds on the
        # deterministic path and the boot unread scan, not only on the
        # cognitive completion path).
        session_mail = payload.get("mail") if isinstance(payload, dict) else None
        for request in requests:
            await self._execute_action(
                request,
                session_mail=session_mail,
                trigger_event=trigger_event,
                payload=payload,
            )

    def _evaluate_rule(self, rule: MemberRule, env: dict[str, Any]) -> list[ActionRequest]:
        """Evaluate a deterministic rule's ``:when`` gate and action forms.

        With ``rule.fuel`` set (phase D) the gate and all actions share one
        combined fuel counter — ``:fuel`` is the rule's total evaluation
        budget, and a runaway gate eats into the action allowance. Without
        it the ``:when`` gate runs under the independent
        :data:`DEFAULT_WHEN_FUEL` and each action under
        :data:`DEFAULT_FUEL`. A false gate short-circuits (no actions).
        """
        if rule.fuel is not None:
            evaluator = _Evaluator(self._builtins, rule.fuel, DEFAULT_MAX_DEPTH)
            if rule.when is not None and not evaluator.eval(rule.when, dict(env), 0):
                return []
            results = [evaluator.eval(action, dict(env), 0) for action in rule.actions]
            return self._collect_results(results)
        if rule.when is not None and not eval_form(
            rule.when, env, self._builtins, fuel=DEFAULT_WHEN_FUEL
        ):
            return []
        results = [
            eval_form(action, env, self._builtins, fuel=DEFAULT_FUEL)
            for action in rule.actions
        ]
        return self._collect_results(results)

    @staticmethod
    def _collect_results(results: list[Any]) -> list[ActionRequest]:
        """Extract the ActionRequests a rule's action forms produced.

        Each action evaluates to a single request, a list of requests
        (``start-all``, or a ``while`` loop collecting several), or
        nothing — all other values are ignored.
        """
        requests: list[ActionRequest] = []
        for result in results:
            if isinstance(result, ActionRequest):
                requests.append(result)
            elif isinstance(result, list):
                requests.extend(
                    item for item in result if isinstance(item, ActionRequest)
                )
        return requests

    # ── Unified action executor ──

    async def _execute_action(
        self,
        request: ActionRequest,
        session_mail: Mail | None = None,
        *,
        trigger_event: Event | None = None,
        payload: Any = None,
    ) -> None:
        """Execute one action natively — all five kinds, one executor.

        ``start`` / ``stop`` become lifecycle request decisions for the
        registry; ``forward`` / ``emit`` build and send a new mail;
        ``spawn`` delegates to the registry's spawn pipeline. The trace
        carrier passes through from the triggering event when present, else
        from the current span (a running session) — the decision joins its
        trigger's trace tree.
        """
        self._last_action_at = time.monotonic()
        kind = request.kind
        if kind == "spawn":
            await self._execute_spawn(request)
        elif kind == "emit":
            await self._execute_emit(request, session_mail, trigger_event)
        elif kind == "start":
            await self._publish_request(
                EventType.AGENT_START_REQUEST,
                StartRequest(
                    agent_id=request.data["agent_id"], reason=request.data["reason"]
                ),
                trigger_event,
                action="start",
            )
        elif kind == "stop":
            await self._publish_request(
                EventType.AGENT_STOP_REQUEST,
                StopRequest(
                    agent_id=request.data["agent_id"], reason=request.data["reason"]
                ),
                trigger_event,
                action="stop",
            )
        elif kind == "forward":
            await self._execute_forward(request, payload, session_mail, trigger_event)
        else:
            logger.warning(
                "member_action_unknown",
                member_id=self._member_id,
                action=kind,
            )

    async def _publish_request(
        self,
        event_type: EventType,
        request_payload: StartRequest | StopRequest,
        trigger_event: Event | None,
        *,
        action: str,
    ) -> None:
        """Publish a lifecycle request decision, joining the trigger's trace."""
        logger.info(
            "member_action_requested",
            member_id=self._member_id,
            action=action,
            agent_id=request_payload.agent_id,
            reason=request_payload.reason,
        )
        await self.publish(
            Event(
                type=event_type,
                payload=request_payload,
                source=self.member_id,
                trace_id=trigger_event.trace_id if trigger_event else None,
                span_id=trigger_event.span_id if trigger_event else None,
                parent_span_id=trigger_event.parent_span_id if trigger_event else None,
            )
        )

    async def _execute_emit(
        self,
        request: ActionRequest,
        session_mail: Mail | None,
        trigger_event: Event | None,
    ) -> None:
        """Emit a mail; an emit without explicit recipients replies to the
        sender of the session's trigger mail (completion path)."""
        to = request.data["to"] or ([session_mail.from_address] if session_mail else [])
        carrier = self._carrier_for(trigger_event)
        await self._send_mail(
            to=to,
            content=request.data["content"],
            in_reply_to=session_mail.id if session_mail else None,
            thread_id=session_mail.thread_id if session_mail else None,
            carrier=carrier,
        )

    async def _execute_forward(
        self,
        request: ActionRequest,
        payload: Any,
        session_mail: Mail | None,
        trigger_event: Event | None,
    ) -> None:
        """Forward the triggering mail: build a new Mail and publish MAIL_NEW.

        The new mail is from the member's own address, carries the original
        content, and links back via ``forward_of``. A trigger without a mail
        (a boot rule, a non-mail event) skips with a warning; a malformed
        recipient in the spec fails the same way.
        """
        source = (
            session_mail
            if session_mail is not None
            else (payload.get("mail") if isinstance(payload, dict) else None)
        )
        if not isinstance(source, Mail):
            logger.warning(
                "member_forward_without_mail",
                member_id=self._member_id,
            )
            return
        try:
            forwarded = Mail.create(
                from_address=self.address,
                to=request.data["to"],
                cc=request.data["cc"],
                subject=request.data["subject"],
                content=source.content,
                forward_of=source.id,
            )
        except ValueError as e:
            logger.warning(
                "member_forward_invalid",
                member_id=self._member_id,
                error=str(e),
            )
            return
        logger.info(
            "member_action_requested",
            member_id=self._member_id,
            action="forward",
            mail_id=forwarded.id,
            forward_of=source.id,
            to=forwarded.to,
        )
        await self._publish_mail(forwarded, self._carrier_for(trigger_event))

    async def _execute_spawn(self, request: ActionRequest) -> None:
        """Spawn a new member through the registry's full trust gate and
        spawn budget (stage 6b); the session-id binding for quota
        attribution comes from structlog contextvars when inside a session."""
        registry = self._registry
        if registry is None:
            raise RuntimeError("spawn action unavailable: member has no registry")
        prototype = request.data["prototype"]
        name = request.data["name"] or f"{prototype}-{uuid.uuid4().hex[:4]}"
        session_id = get_contextvars().get("session_id")
        await registry.spawn_member(
            prototype=prototype,
            name=name,
            spawner_id=self._member_id,
            session_id=str(session_id) if session_id else None,
        )

    # ── Mail sending / read-marking ──

    def _carrier_for(self, trigger_event: Event | None) -> Carrier:
        """The trace carrier for a decision: the triggering event's span
        tree when there is one, else the current (session) span, else none."""
        if trigger_event is not None:
            return (
                trigger_event.trace_id,
                trigger_event.span_id,
                trigger_event.parent_span_id,
            )
        current = get_current_trace()
        if current is not None:
            return (current.trace_id, current.span_id, current.parent_span_id)
        return (None, None, None)

    async def _send_mail(
        self,
        *,
        to: list[str],
        content: str,
        cc: list[str] | None = None,
        subject: str | None = None,
        in_reply_to: str | None = None,
        thread_id: str | None = None,
        carrier: Carrier = (None, None, None),
    ) -> None:
        """Build a mail from this member and publish MAIL_NEW on the bus.

        A probationary member's mentor () is cc'd unless already a
        recipient (to or cc) — no duplicate copies.

        Never raises: a mail failure is logged, not propagated (a failing
        action must not break the bus dispatch or the session lifecycle).
        """
        try:
            recipients_cc = [*(cc or [])]
            if (
                self._mentor_cc is not None
                and self._mentor_cc not in to
                and self._mentor_cc not in recipients_cc
            ):
                recipients_cc.append(self._mentor_cc)
            mail = Mail.create(
                from_address=self.address,
                to=to,
                cc=recipients_cc,
                subject=subject,
                content=content,
                in_reply_to=in_reply_to,
                thread_id=thread_id,
            )
        except ValueError as e:
            logger.warning(
                "member_mail_invalid",
                member_id=self._member_id,
                error=str(e),
            )
            return
        await self._publish_mail(mail, carrier)

    async def _publish_mail(self, mail: Mail, carrier: Carrier) -> None:
        """Publish a built mail as MAIL_NEW with the given trace carrier."""
        trace_id, span_id, parent_span_id = carrier
        await self.publish(
            Event(
                type=EventType.MAIL_NEW,
                payload=mail,
                source=self.member_id,
                trace_id=trace_id,
                span_id=span_id,
                parent_span_id=parent_span_id,
            )
        )

    async def _mark_read(self, mail: Mail | None) -> None:
        """Mark a processed mail read; failures are logged only."""
        if mail is None or self._mailbox is None:
            return
        self._scan_pending.discard(mail.id)
        try:
            await self._mailbox.mark_read(mail.id)
        except Exception as e:
            logger.warning(
                "member_mail_mark_read_failed",
                member_id=self._member_id,
                mail_id=mail.id,
                error=str(e),
            )

    # ── Unread rescue scan (the mailbox is the durable queue) ──

    async def _scan_unread_mail(self) -> None:
        """Replay unread inbox mail through the matching rules.

        Runs at boot (mail that arrived while the member slept) and from
        the rescan task (deferred overflow trickle and the periodic
        sweep). Cognitive members gate and enqueue unread mail (a
        rejected mail is marked read now, D6.1) under two throttles (issue ): one
        thread merges into one session — the thread's latest unread mail
        is the representative, its earlier siblings are marked read as
        covered (the session's mail-history context renders the whole
        thread) — and overflow the bounded queue cannot take stays fully
        unread, deterministic rules deferred with it, for the deferred
        rescan (falling through to read-marking here used to lose the
        mail without a session). Deterministic members run the mail rules
        inline. Failures of the scan itself are logged and skip the scan
        only.
        """
        if self._mailbox is None:
            return
        try:
            unread = await self._mailbox.unread()
        except Exception as e:
            logger.error(
                "member_mail_unread_scan_failed",
                member_id=self._member_id,
                error=str(e),
            )
            return
        cognitive_scan = (
            self._cognitive is not None
            and self._cognitive_rule is not None
            and self._cognitive_rule.on is EventType.MAIL_DELIVERED
        )
        # Merge candidates: own mail is read without processing, and a
        # mail a previous scan enqueued still belongs to its session —
        # neither takes part in thread merging.
        candidates = [
            mail
            for mail in unread
            if mail.from_address != self.address and mail.id not in self._scan_pending
        ]
        merged = self._merged_thread_ids(candidates) if cognitive_scan else set()
        dropped = False
        for mail in unread:
            if mail.from_address == self.address:
                await self._mark_read(mail)
                continue
            if mail.id in self._scan_pending:
                continue  # a previous scan's session owns this mail's read
            cognitive_deferred = False
            if cognitive_scan and mail.id not in merged:
                assert self._cognitive_rule is not None
                result = await self._enqueue_cognitive(
                    self._cognitive_rule, mail, trigger_event=None
                )
                if result == "queued":
                    cognitive_deferred = True
                    self._scan_pending.add(mail.id)
                elif result == "dropped":
                    # Bounded queue full: leave the mail fully unread —
                    # the deferred rescan picks it up once the scheduler
                    # drains.
                    dropped = True
                    continue
            # Deterministic MAIL_DELIVERED rules always run (phase F: a mixed
            # member's cheap path — auto-optout deflection — fires even when
            # a cognitive rule also matches this mail). Read is deferred only
            # when a session was queued (D6.1), mirroring the live dispatch.
            payload = {"mail": mail, "recipient": self._member_id}
            for rule in self._spec.rules:
                if rule.on is EventType.MAIL_DELIVERED and rule.loop is None:
                    await self._run_rule(rule, trigger_event=None, payload=payload)
            if not cognitive_deferred:
                await self._mark_read(mail)
        self._scan_deferred = dropped
        if unread:
            logger.info(
                "member_mail_unread_scanned",
                member_id=self._member_id,
                count=len(unread),
                merged=len(merged),
                deferred=dropped,
            )

    @staticmethod
    def _merged_thread_ids(mails: list[Mail]) -> set[str]:
        """Ids of unread mails covered by a later sibling in the same thread.

        One thread = one session: the latest unread mail of a thread is the
        representative (enqueued for the cognitive rule); every earlier
        sibling merges into it.
        """
        representative: dict[str, str] = {}
        for mail in mails:  # chronological, the latest wins
            representative[mail.thread_id] = mail.id
        return {mail.id for mail in mails if mail.id != representative[mail.thread_id]}

    async def _rescan_loop(self) -> None:
        """The member's one background mail-rescan task (issue ).

        Two cadences, never two tasks: while the last scan deferred
        overflow (bounded queue full), poll every
        ``_RESCAN_POLL_SECONDS`` and re-scan as soon as the scheduler
        has queue capacity; otherwise re-scan every
        ``mail_rescan_interval_seconds`` so live mail a full queue
        dropped is picked up while the member runs (a boot member
        never reboots, so there is no "next boot scan" for it). A
        periodic sweep that itself hits a full queue flips back into
        the drain-trickle cadence.

        The scan is idempotent and LLM-free: it only enqueues sessions,
        and ``_scan_pending`` skips mail whose session already owns the
        read — a sweep over an empty inbox enqueues nothing.
        """
        assert self._cognitive is not None
        interval = self._cognitive.config.mail_rescan_interval_seconds
        next_sweep = time.monotonic() + interval
        while True:
            if self._scan_deferred:
                await asyncio.sleep(_RESCAN_POLL_SECONDS)
            else:
                await asyncio.sleep(max(0.0, next_sweep - time.monotonic()))
            if not self._initialized or self._scheduler is None:
                return
            if self._scan_deferred and not self._scheduler.has_queue_capacity:
                continue
            await self._scan_unread_mail()
            if not self._scan_deferred:
                next_sweep = time.monotonic() + interval

    # ── Cognitive assembly (member_spec rule → LoopDriver engine) ──

    def _assemble_cognitive(self, caps: CognitiveCapabilities) -> None:
        """Assemble the cognitive engine from capabilities — the injected
        services, plain data, and closures over the event bus — never a
        back-reference to this runtime."""
        rule = self._cognitive_rule
        assert rule is not None and rule.loop is not None

        self._logger = logger.bind(agent_id=self._member_id)

        identity = AgentIdentity(id=self._member_id, name=self._name, address=self.address)
        publisher = make_event_publisher(self._event_bus, self._name)
        # Session event journal (ADR 0007): one append-only log per session,
        # written by the driver, read by the scheduler (resume on boot), the
        # reflection digest, the distillation trigger (ADR 0017) and the
        # context builder (thread-recap section).
        journal = SessionJournal(self._member_id, caps.tlon_paths)
        self._context_builder = ContextBuilder(
            caps.config,
            caps.team_members,
            self._mailbox,
            caps.memory,
            caps.task_manager,
            rule,
            self.address,
            journal,
            caps.tlon_paths,
        )
        self._reflection = ReflectionEngine(caps.memory, caps.llm_client, rule, journal)
        self._stage_services = StageServices(
            identity=identity,
            source_hash=self._spec.source_hash,
            llm_client=caps.llm_client,
            context_builder=self._context_builder,
            publish=publisher,
            tool_definitions=caps.tool_executor.get_tool_definitions,
            logger=self._logger,
        )
        self._effect_executor = EffectExecutor(
            identity=identity,
            rule=rule,
            mailbox=self._mailbox,
            publish=publisher,
            logger=self._logger,
            mentor_cc=self._mentor_cc,
        )
        on_complete = OnCompleteRunner(
            identity, rule, self._logger, execute_action=self._execute_action
        )
        self._loop_driver = LoopDriver(
            identity=identity,
            rule=rule,
            services=self._stage_services,
            executor=self._effect_executor,
            tool_executor=caps.tool_executor,
            per_tool_timeout=caps.per_tool_timeout,
            on_complete=on_complete,
            journal=journal,
            spill=ToolSpill(caps.tlon_paths, self._member_id),
            # Context pressure relief (ADR 0008): same journal, the
            # model's context window — resolved to an int at assembly
            # (ADR 0012 phase 2); the fallback covers capabilities built
            # without assembly (tests), whose config.llm may be None.
            pressure=ContextPressure(
                caps.llm_client,
                journal,
                (caps.config.llm.context_window if caps.config.llm else None)
                or DEFAULT_CONTEXT_WINDOW,
            ),
        )
        self._scheduler = SessionScheduler(
            caps.config,
            caps.memory,
            self._stage_services.session_span,
            self._mark_read,
            self._reflection.check_trigger,
            self._reflection.note_interaction,
            self._loop_driver,
            journal,
        )

    def _env(self, payload: Any) -> dict[str, Any]:
        """The unified evaluation env: always ``{msg, event, self}`` —
        ``msg`` is the payload's mail or None (non-mail events make the msg
        predicates safely return False), ``event`` is the payload, ``self``
        carries the member's id/name/address (the address is pre-derived
        here so the DSL builtins read data, never call back into tlon —
        ADR 0018 §3)."""
        mail = payload.get("mail") if isinstance(payload, dict) else None
        return {
            "msg": mail,
            "event": payload,
            "self": {
                "id": self._member_id,
                "name": self._name,
                "address": address_for_member(self._member_id),
            },
        }
