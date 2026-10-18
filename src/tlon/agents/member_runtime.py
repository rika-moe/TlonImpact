"""Member programs execute one serial stream of effects per member."""

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
from tlon.agents.member_spec import MemberRule, MemberSpec, SessionRecipe
from tlon.agents.reflection import ReflectionEngine
from tlon.agents.scheduler import SessionScheduler
from tlon.agents.session import AgentSession
from tlon.agents.session_journal import SessionJournal
from tlon.agents.stages.services import StageServices, make_event_publisher
from tlon.agents.tool_executor import ToolExecutor
from tlon.agents.trigger import AgentIdentity
from tlon.agents.vocabulary import collect_member_effects, rule_env
from tlon.base.component import Component
from tlon.base.config import DEFAULT_CONTEXT_WINDOW, AgentConfig
from tlon.base.device import Device
from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus
from tlon.base.llm_client import LLMClient
from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths
from tlon.base.tracing.trace_context import continue_trace, get_current_trace
from tlon.memory.manager import MemoryManager
from tlon.organization.mail.mail import Mail, address_for_member
from tlon.organization.mail.mailbox import Mailbox
from tlon.organization.mail.post_office import PostOffice, PostRejected
from tlon.organization.mail.store import MailStore
from tlon.organization.messaging.member import Member
from tlon.tools.spill import ToolSpill
from tlon.tools.tasks.manager import TaskManager
from tlosure.builtins import ActionRequest, Builtin, EvalError, FuelExhaustedError
from tlosure.edn import dumps, loads
from tlosure.eval import (
    DEFAULT_FUEL,
    DEFAULT_MAX_DEPTH,
    DEFAULT_WHEN_FUEL,
    Evaluator,
)

if TYPE_CHECKING:
    from tlon.agents.agent_registry import AgentRegistry
    from tlon.sandbox.device import ExecutionDevice

logger = get_logger(__name__)

#: Trace carrier triple for forwarding an event's span tree to a decision.
Carrier = tuple[str | None, str | None, str | None]


class MemberState(Enum):
    """Internal lifecycle diagnostics, separate from public scheduling states."""

    INITIALIZING = "initializing"  # Starting up
    ACTIVE = "active"  # Running and processing messages
    THINKING = "thinking"  # Currently processing a message
    ERROR = "error"  # Encountered an error
    SHUTDOWN = "shutdown"  # Stopped


@dataclass
class MemberCapabilities:
    """Services available to a member program; assembled lazily by the runtime."""

    config: AgentConfig
    llm_client: LLMClient
    memory: MemoryManager
    task_manager: TaskManager
    tool_executor: ToolExecutor
    tlon_paths: TlonPaths
    per_tool_timeout: float = 30.0
    registry: "AgentRegistry | None" = None
    # Trust ladder (docs/design/unified-member-model.md): a probationary
    # member's mentor address (its spawner), cc'd on composed outbound
    # mail; None for full members.
    mentor_cc: str | None = None
    # The member's execution device (docs/design/execution-device.md):
    # assembly owns it and
    # shutdown closes it — a wake re-assembly must not leak the previous
    # client's connection pool. Typed as the ExecutionDevice interface
    # (issue #21): assembly injects the AuditedExecutionDevice-wrapped
    # DockerRuntime; close() passes through to the Docker client. None
    # for members declaring docker.level=L0 (无执行装置, issue #26),
    # when the daemon was unreachable at assembly time, and for
    # capability sets built without assembly (Adam, tests).
    docker_runtime: "ExecutionDevice | None" = None


class MemberRuntime(Member):
    """Execute matching event rules through one serial member-owned worker."""

    def __init__(
        self,
        *,
        member_id: str,
        name: str,
        event_bus: EventBus,
        spec: MemberSpec,
        builtins: dict[str, Builtin],
        mailbox: Mailbox | None = None,
        post_office: PostOffice | None = None,
        capabilities: MemberCapabilities | None = None,
        registry: "AgentRegistry | None" = None,
        tlon_paths: TlonPaths | None = None,
    ) -> None:
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
                carries ``~/.tlon/adam/mail/`` — docs/design/mail.md
                「守护者信箱」)
            capabilities: The member's capability set (LLM/memory/tools).
                Programs request sessions explicitly with run-loop.
                Bare host-service members may omit these services.
            registry: Agent registry for ``spawn`` actions (None, e.g.
                Adam, disables them)
        """
        Component.__init__(self, name=name)
        Device.__init__(self, event_bus=event_bus, name=name)
        self._member_id = member_id
        self._spec = spec
        self._builtins = builtins
        self._mailbox = mailbox
        self._post_office = post_office
        self._capabilities = capabilities
        self._registry = (
            registry if registry is not None else (capabilities.registry if capabilities else None)
        )
        # Trust ladder (docs/design/unified-member-model.md): the
        # probation mentor's address, cc'd on composed outbound mail
        # (_send_mail; the session path's
        # EffectExecutor gets it in _assemble_session). Forwarding is
        # not cc'd — passing along an existing mail is noise to the mentor.
        self._mentor_cc = capabilities.mentor_cc if capabilities is not None else None
        # Rule idle bookkeeping (registry drain): initialize time
        # and the last executed rule action, both monotonic.
        self._initialized_at: float | None = None
        self._last_action_at: float | None = None
        self._active_actions = 0
        # Session services are initialized when a program requests a session.
        self._scheduler: SessionScheduler | None = None
        self._loop_driver: LoopDriver | None = None
        self._effect_executor: EffectExecutor | None = None
        self._stage_services: StageServices | None = None
        self._reflection: ReflectionEngine | None = None
        self._context_builder: ContextBuilder | None = None
        self._logger = logger.bind(agent_id=member_id)
        self._state: MemberState | None = None
        # The one background mail-rescan task (ADR 0028 决策 4: boot scan +
        # the slow periodic sweep share it).
        self._rescan_task: asyncio.Task[None] | None = None
        self._queue: asyncio.Queue[Event] = asyncio.Queue()
        self._queued_mail: set[str] = set()
        self._completed_dispatches: set[tuple[str, str]] = set()
        self._program_task: asyncio.Task[None] | None = None
        self._journal = (
            SessionJournal(member_id, capabilities.tlon_paths)
            if capabilities
            else (SessionJournal(member_id, tlon_paths) if tlon_paths is not None else None)
        )
        if capabilities is not None:
            self._assemble_scheduler(capabilities)

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
        """Public scheduling state; I/O and background reflection remain active."""
        return "active" if self.active_session_count > 0 else "waiting"

    def get_diagnostic_error(self) -> str | None:
        """Runtime failure is a diagnostic, never a scheduling state."""
        return "runtime error" if self._state is MemberState.ERROR else None

    @property
    def active_session_count(self) -> int:
        """Count all live work: sessions, reflection and rule handlers.

        Draining must wait for every kind of work, including external I/O.
        """
        if self._scheduler is not None:
            return (
                self._scheduler.active_session_count
                + self._active_actions
                + int(self._reflection is not None and self._reflection.is_running)
            )
        return self._active_actions

    @property
    def idle_seconds(self) -> float:
        """Idle time covers rule effects, sessions and background maintenance."""
        if self.active_session_count > 0 or self._initialized_at is None:
            return 0.0
        since = self._last_action_at or self._initialized_at
        elapsed = time.monotonic() - since
        return min(elapsed, self._scheduler.idle_seconds) if self._scheduler else elapsed

    # ── Lifecycle ──

    async def initialize(self) -> None:
        """Load services, recover sessions, and start the one program worker."""
        self._state = MemberState.INITIALIZING
        if self._capabilities is not None:
            await self._capabilities.memory.load()
            self._capabilities.task_manager.load()
        self._initialized = True
        self._initialized_at = time.monotonic()
        if self._scheduler is not None:
            self._scheduler.start()
            await self._scheduler.wait_ready()
        event_types = {rule.on for rule in self._spec.rules if rule.on is not None}
        for event_type in sorted(event_types, key=str):
            self.subscribe(event_type, self._on_event)
        for rule in self._spec.rules:
            if rule.on is None:
                await self._run_rule(rule, trigger_event=None)
        self._program_task = asyncio.create_task(self._program_loop())
        await self._scan_unread_mail()
        if self._mailbox is not None:
            self._rescan_task = asyncio.create_task(self._rescan_loop())
        self._state = MemberState.ACTIVE

    async def shutdown(self) -> None:
        """Shutdown the member: cancel the background tasks (rescan,
        in-flight reflection), stop the session loop, release
        subscriptions, shut down tools (cancelling background bash tasks),
        and close the Docker client (its connection pool must not leak
        across sleep→wake re-assembly)."""
        with bound_contextvars(agent_id=self._member_id):
            if self._rescan_task is not None:
                self._rescan_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._rescan_task
                self._rescan_task = None
            if self._program_task is not None:
                self._program_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._program_task
                self._program_task = None
            if self._scheduler is not None:
                await self._scheduler.stop()
            if self._reflection is not None:
                # The scheduler is stopped, so no new reflection can
                # trigger; cancel one already in flight — no
                # free-floating task survives the member.
                await self._reflection.shutdown()
            self.unsubscribe_all()
            if self._capabilities is not None:
                await self._capabilities.tool_executor.shutdown()
                # Tools first (a background bash task may still be using
                # the runtime), then the Docker client — its connection
                # pool must not leak across sleep→wake re-assembly
                # (docs/design/execution-device.md). A close failure is
                # logged, never blocking.
                docker_runtime = self._capabilities.docker_runtime
                if docker_runtime is not None:
                    try:
                        docker_runtime.close()
                    except Exception as e:
                        logger.warning(
                            "docker_runtime_close_error",
                            member_id=self._member_id,
                            error=str(e),
                        )
            if self._scheduler is not None:
                self._state = MemberState.SHUTDOWN
            self._initialized = False
            self._initialized_at = None
            logger.info("member_runtime_shutdown", member_id=self._member_id)

    # ── Event dispatch ──

    async def _on_event(self, event: Event) -> None:
        if not self._can_schedule():
            return
        mail = event.payload.get("mail") if isinstance(event.payload, dict) else None
        if event.type is EventType.MAIL_DELIVERED:
            if not isinstance(mail, Mail) or event.payload.get("recipient") != self._member_id:
                return
            if mail.from_address == self.address:
                await self._mark_read(mail)
                return
            if mail.id in self._queued_mail:
                return
            self._queued_mail.add(mail.id)
        self._queue.put_nowait(event)

    def _can_schedule(self) -> bool:
        return self._registry is None or self._registry.can_schedule(self._member_id)

    async def _program_loop(self) -> None:
        while True:
            event = await self._queue.get()
            mail = event.payload.get("mail") if isinstance(event.payload, dict) else None
            try:
                if self._can_schedule():
                    self._active_actions += 1
                    try:
                        if (
                            self._registry is not None
                            and event.type is not EventType.AGENT_STATE_CHANGED
                        ):
                            await self._registry.publish_scheduling_state(self._member_id)
                        await self._dispatch_event(event)
                    finally:
                        self._active_actions -= 1
                        if (
                            self._registry is not None
                            and event.type is not EventType.AGENT_STATE_CHANGED
                        ):
                            await self._registry.publish_scheduling_state(self._member_id)
            except Exception as error:
                logger.error(
                    "member_program_failed",
                    member_id=self._member_id,
                    error=str(error),
                    exc_info=True,
                )
            finally:
                if isinstance(mail, Mail):
                    self._queued_mail.discard(mail.id)
                self._queue.task_done()

    async def _dispatch_event(self, event: Event) -> None:
        mail = event.payload.get("mail") if isinstance(event.payload, dict) else None
        for rule in self._spec.rules:
            if rule.on != event.type or rule.on is None or not self._can_schedule():
                continue
            if isinstance(mail, Mail) and await self._dispatch_completed(mail.id, rule.name):
                continue
            await self._run_rule(rule, trigger_event=event)
            if isinstance(mail, Mail):
                await self._complete_dispatch(mail.id, rule.name)

    async def _dispatch_completed(self, mail_id: str, rule_name: str) -> bool:
        key = (mail_id, rule_name)
        if key in self._completed_dispatches:
            return True
        if self._journal is not None:
            events = await self._journal.read_events(f"dispatch:{mail_id}:{rule_name}")
            if any(event["type"] == "dispatch/complete" for event in events):
                self._completed_dispatches.add(key)
                return True
        return False

    async def _complete_dispatch(self, mail_id: str, rule_name: str) -> None:
        if self._journal is not None:
            await self._journal.append(f"dispatch:{mail_id}:{rule_name}", "dispatch/complete")
        self._completed_dispatches.add((mail_id, rule_name))
        if len(self._completed_dispatches) > 4096:
            self._completed_dispatches.pop()

    # ── Rule evaluation ──

    async def _run_rule(
        self,
        rule: MemberRule,
        trigger_event: Event | None,
        payload: Any = None,
    ) -> None:
        if self._registry is not None and not self._registry.can_schedule(self._member_id):
            return
        self._active_actions += 1
        try:
            if trigger_event is None and self._registry is not None:
                await self._registry.publish_scheduling_state(self._member_id)
            await self._run_rule_body(rule, trigger_event, payload)
        finally:
            self._active_actions -= 1
            if trigger_event is None and self._registry is not None:
                await self._registry.publish_scheduling_state(self._member_id)

    async def _run_rule_body(
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
                budget=e.budget,
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
        session_mail = payload.get("mail") if isinstance(payload, dict) else None
        for index, request in enumerate(requests):
            if request.kind == "run_loop":
                await self._run_session_effect(
                    request, rule.name, requests[index + 1 :], session_mail, trigger_event
                )
            else:
                await self._execute_action(
                    request,
                    session_mail=session_mail,
                    trigger_event=trigger_event,
                    payload=payload,
                )

    def _evaluate_rule(self, rule: MemberRule, env: dict[str, Any]) -> list[ActionRequest]:
        """Evaluate a rule's ``:when`` gate and action forms.

        With ``rule.fuel`` set (phase D) the gate and all actions share one
        combined fuel counter — ``:fuel`` is the rule's total evaluation
        budget, and a runaway gate eats into the action allowance. Without
        it the ``:when`` gate runs under the independent
        :data:`DEFAULT_WHEN_FUEL` and each action under
        :data:`DEFAULT_FUEL`. A false gate short-circuits (no actions).

        All forms — gate included — share one env dict (ADR 0021): a
        ``defn`` in any form is visible to the forms after it. Evaluation
        goes through bare :class:`Evaluator` instances rather than
        :func:`eval_form` (whose entry copies the env) so definitions
        persist across forms; the two paths differ only in how many fuel
        budgets the forms draw from.
        """
        with collect_member_effects() as requests:
            if rule.fuel is not None:
                evaluator = Evaluator(self._builtins, rule.fuel, DEFAULT_MAX_DEPTH)
                if rule.when is not None and not evaluator.eval(rule.when, env, 0):
                    return []
                for action in rule.actions:
                    evaluator.eval(action, env, 0)
                return requests
            if rule.when is not None and not Evaluator(
                self._builtins, DEFAULT_WHEN_FUEL, DEFAULT_MAX_DEPTH
            ).eval(rule.when, env, 0):
                return []
            for action in rule.actions:
                Evaluator(self._builtins, DEFAULT_FUEL, DEFAULT_MAX_DEPTH).eval(action, env, 0)
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
        if (
            kind in {"start", "stop"}
            and self._member_id != "adam"
            and request.data["agent_id"] != self._member_id
        ):
            raise ValueError("members may only manage their own scheduling state")
        if kind == "mark_read":
            await self._mark_read(session_mail)
        elif kind == "spawn":
            await self._execute_spawn(request)
        elif kind == "emit":
            await self._execute_emit(request, session_mail, trigger_event)
        elif kind == "start":
            await self._publish_request(
                EventType.AGENT_START_REQUEST,
                StartRequest(agent_id=request.data["agent_id"], reason=request.data["reason"]),
                trigger_event,
                action="start",
            )
        elif kind == "stop":
            await self._publish_request(
                EventType.AGENT_STOP_REQUEST,
                StopRequest(agent_id=request.data["agent_id"], reason=request.data["reason"]),
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
        """Send a mail, replying to the actual trigger when recipients are omitted."""
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
        """Build a mail and submit through the DSL best-effort effect boundary.

        A probationary member's mentor (trust ladder,
        docs/design/unified-member-model.md) is cc'd unless already a
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
        """Isolate DSL mail failures without changing tool submission semantics.

        Rejection and uncertainty are observable in structured logs; no
        postmaster notice is manufactured and cancellation still propagates.
        """
        try:
            if self._post_office is None:
                raise RuntimeError("This member has no post office")
            trace_id, span_id, _ = carrier
            if trace_id:
                with continue_trace(trace_id, span_id):
                    await self._post_office.post(mail)
            else:
                await self._post_office.post(mail)
        except PostRejected as error:
            self._logger.warning(
                "dsl_mail_rejected",
                mail_id=mail.id,
                reason=error.reason,
                invalid_recipients=error.invalid_recipients,
                error=str(error),
            )
        except Exception as error:
            self._logger.error(
                "dsl_mail_submission_unknown",
                mail_id=mail.id,
                request_id=mail.id,
                error=str(error),
                exc_info=True,
            )

    async def _mark_read(self, mail: Mail | None) -> None:
        """Mark a processed mail read; failures are logged only."""
        if mail is None or self._mailbox is None:
            return
        try:
            await self._mailbox.mark_read(mail.id)
        except Exception as e:
            logger.warning(
                "member_mail_mark_read_failed",
                member_id=self._member_id,
                mail_id=mail.id,
                error=str(e),
            )

    async def _scan_unread_mail(self) -> None:
        if not self._can_schedule() or self._mailbox is None:
            return
        try:
            for mail in await self._mailbox.unread():
                if all(
                    [
                        await self._dispatch_completed(mail.id, rule.name)
                        for rule in self._spec.rules
                        if rule.on is EventType.MAIL_DELIVERED
                    ]
                ):
                    continue
                await self._on_event(
                    Event(
                        type=EventType.MAIL_DELIVERED,
                        payload={"mail": mail, "recipient": self._member_id},
                        source="mail-scan",
                    )
                )
        except Exception as error:
            logger.error(
                "member_mail_unread_scan_failed", member_id=self._member_id, error=str(error)
            )

    async def _rescan_loop(self) -> None:
        interval = (
            self._capabilities.config.mail_rescan_interval_seconds if self._capabilities else 30
        )
        while True:
            await asyncio.sleep(interval)
            await self._scan_unread_mail()

    def _assemble_scheduler(self, caps: MemberCapabilities) -> None:
        assert self._journal is not None
        registry = self._registry

        async def check_reflection() -> None:
            if self._reflection is not None:
                await self._reflection.check_trigger()

        def note_interaction() -> None:
            if self._reflection is not None:
                self._reflection.note_interaction()

        def session_span(session: AgentSession) -> Any:
            assert self._stage_services is not None
            return self._stage_services.session_span(session)

        self._scheduler = SessionScheduler(
            caps.config,
            caps.memory,
            session_span,
            check_reflection,
            note_interaction,
            lambda recipe: self._assemble_session(caps, recipe),
            self._journal,
            self._mailbox,
            can_schedule=self._can_schedule,
            state_changed=(lambda: registry.publish_scheduling_state(self._member_id))
            if registry
            else None,
            apply_scheduling_request=(
                lambda request: registry.apply_scheduling_request(self._member_id, request)
            )
            if registry
            else None,
            resume_invocation=self._resume_invocation,
        )

    async def _run_session_effect(
        self,
        request: ActionRequest,
        rule_name: str,
        tail: list[ActionRequest],
        mail: Mail | None,
        event: Event | None,
    ) -> None:
        if self._scheduler is None:
            raise RuntimeError("run-loop requires member services")
        recipe = SessionRecipe.from_edn(request.data["recipe"])
        invocation = {
            "rule_name": rule_name,
            "tail": [{"kind": action.kind, "data_edn": dumps(action.data)} for action in tail],
        }
        trigger = mail.content if mail else f"Event: {event.type.value if event else 'boot'}"
        await self._scheduler.run_invocation(
            recipe,
            trigger,
            [mail] if mail else [],
            {
                "trace_id": event.trace_id if event else None,
                "span_id": event.span_id if event else None,
            },
            invocation,
        )

    async def _resume_invocation(self, session: AgentSession) -> None:
        mail = (
            await self._mailbox.find(session.source_mail_id)
            if self._mailbox is not None and session.source_mail_id
            else None
        )
        requests = [
            ActionRequest(kind=item["kind"], data=loads(item["data_edn"]))
            for item in session.invocation.get("tail", [])
        ]
        for index, request in enumerate(requests):
            if request.kind == "run_loop":
                await self._run_session_effect(
                    request, session.invocation["rule_name"], requests[index + 1 :], mail, None
                )
            else:
                await self._execute_action(request, session_mail=mail, payload={"mail": mail})
        if session.source_mail_id and session.invocation.get("rule_name"):
            await self._complete_dispatch(session.source_mail_id, session.invocation["rule_name"])

    def _assemble_session(self, caps: MemberCapabilities, recipe: SessionRecipe) -> LoopDriver:
        """Assemble the session engine from capabilities — the injected
        services, plain data, and closures over the event bus — never a
        back-reference to this runtime."""
        registry = caps.registry

        self._logger = logger.bind(agent_id=self._member_id)

        identity = AgentIdentity(id=self._member_id, name=self._name, address=self.address)
        publisher = make_event_publisher(self._event_bus, self._name)
        # Session event journal (ADR 0007): one append-only log per session,
        # written by the driver, read by the scheduler (resume on boot), the
        # reflection digest, the distillation trigger (ADR 0017) and the
        # context builder (thread-recap section).
        journal = self._journal
        assert journal is not None
        self._context_builder = ContextBuilder(
            caps.config,
            self._mailbox,
            caps.memory,
            caps.task_manager,
            recipe,
            self.address,
            journal,
            tlon_paths=caps.tlon_paths,
        )
        if self._reflection is None:
            self._reflection = ReflectionEngine(
                caps.memory,
                caps.llm_client,
                recipe,
                journal,
                state_changed=(lambda: registry.publish_scheduling_state(self._member_id))
                if registry is not None
                else None,
            )
        else:
            self._reflection.enabled = recipe.reflect.enabled
            self._reflection._interval = recipe.reflect.interval_minutes * 60
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
            mailbox=self._mailbox,
            publish=publisher,
            logger=self._logger,
            mentor_cc=self._mentor_cc,
            post_office=self._post_office,
        )

        def is_human_sender(address: str) -> bool:
            """ADR 0027: termination replies go to human senders only. The
            known-human set is re-resolved on every call in a worker thread;
            humans register when connecting, so the set grows at runtime."""
            humans = {
                address_for_member(h)
                for h in MailStore(caps.tlon_paths.mail_database_file()).known_human_ids()
            }
            return address in humans

        self._loop_driver = LoopDriver(
            identity=identity,
            recipe=recipe,
            services=self._stage_services,
            executor=self._effect_executor,
            tool_executor=caps.tool_executor,
            per_tool_timeout=caps.per_tool_timeout,
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
            is_human_sender=is_human_sender,
            prepare_scheduling_request=(
                registry.prepare_scheduling_request if registry is not None else None
            ),
        )
        return self._loop_driver

    def _env(self, payload: Any) -> dict[str, Any]:
        """The unified evaluation env — the shape is the :class:`RuleEnv`
        contract; construction lives in :func:`vocabulary.rule_env`."""
        return dict(rule_env(payload, member_id=self._member_id, name=self._name))
