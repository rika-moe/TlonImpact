"""ScriptMember — the generic runtime for policy-as-data members (P5a/P5b).

A script member is a deterministic Member whose policy is a
:class:`~tlon.agents.script_spec.ScriptSpec` — pure data describing "on
this event, when this predicate holds, request these actions". The
runtime subscribes the rules' event types, evaluates ``:when`` gates and
action forms with the restricted evaluator (``agents/eval.py`` —
fuel/depth metered, side-effect free), and executes the resulting
ActionRequests natively: ``start`` / ``stop`` publish AGENT_START_REQUEST
/ AGENT_STOP_REQUEST decisions (execution — including all run-state
re-validation — stays the registry's job), ``forward`` builds a new Mail
from the triggering event's mail and publishes it as MAIL_NEW
("the DSL describes, the engine executes").

Host state (registry run-state queries) reaches expressions only through
closure-injected builtins built by the caller
(:func:`~tlon.agents.eval.make_script_builtins`); the runtime itself is
host-agnostic. Evaluation failures are contained: an EvalError skips the
rule with a warning, never the other rules or the bus dispatch. Trace
carriers pass through from the triggering event, so a decision joins its
trigger's trace tree.

Mail semantics (P5b, mirroring the Agent side): a member with an injected
mailbox scans ``mailbox.unread()`` at initialize and runs matching rules
on each mail that arrived while it slept (the boot unread scan); every
processed mail — real-time or rescanned — is marked read (failures are
logged only). Mail sent by the member itself
(``mail.from_address == self.address``) is ignored, aligned with
``Agent._enqueue_mail``'s own-mail skip. Idle semantics for the
registry's drain: ``active_session_count`` is always 0 and
``idle_seconds`` measures the time since the last executed rule action
(from initialize when no action has run yet).

Adam (``agents/adam.py``) is the first configuration instance of this
runtime: a built-in spec plus registry query closures. Registry-spawned
script agents (``runtime="script"`` in agent.yaml) are the second:
their spec comes from ``~/.tlon/{id}/config/script.tlon``.
"""

import time
from typing import Any

from tlon.agents.eval import ActionRequest, Builtin, EvalError, eval_form
from tlon.agents.lifecycle import StartRequest, StopRequest
from tlon.agents.script_spec import ScriptRule, ScriptSpec
from tlon.base.component import Component
from tlon.base.device import Device
from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus
from tlon.base.logger import get_logger
from tlon.organization.mail.mail import Mail
from tlon.organization.mail.mailbox import Mailbox
from tlon.organization.messaging.member import Member

logger = get_logger(__name__)


class ScriptMember(Member):
    """Deterministic policy member driven by a ScriptSpec (policy as data).

    All work happens in the EventBus dispatch context (no worker task; the
    bus already isolates handler exceptions). Decisions are request events
    on the bus; execution is the registry's job.
    """

    def __init__(
        self,
        member_id: str,
        event_bus: EventBus,
        spec: ScriptSpec,
        builtins: dict[str, Builtin],
        mailbox: Mailbox | None = None,
    ):
        """Initialize the script member.

        Args:
            member_id: Member identity on the bus
            event_bus: Event bus for subscriptions and request publishing
            spec: The policy spec (pure data, validated at parse time)
            builtins: Evaluation registry, host predicate closures
                included (built by the caller via make_script_builtins)
            mailbox: The member's mailbox for unread rescue scans and
                read-marking (None for built-in instances like Adam)
        """
        Component.__init__(self, name=f"ScriptMember:{member_id}")
        Device.__init__(self, event_bus=event_bus, name=f"ScriptMember:{member_id}")
        self._member_id = member_id
        self._spec = spec
        self._builtins = builtins
        self._mailbox = mailbox
        # Idle bookkeeping (registry drain): initialize time and the last
        # executed rule action, both monotonic
        self._initialized_at: float | None = None
        self._last_action_at: float | None = None

    @property
    def member_id(self) -> str:
        """Member identity on the bus."""
        return self._member_id

    @property
    def member_type(self) -> str:
        """Member type; script members are deterministic policy members."""
        return "script"

    def get_state(self) -> str:
        """Get member state: "active" once initialized, else "offline"."""
        return "active" if self._initialized else "offline"

    @property
    def active_session_count(self) -> int:
        """Script members run no sessions — always 0 (registry drain reads it)."""
        return 0

    @property
    def idle_seconds(self) -> float:
        """Seconds since the last executed rule action.

        Measured from initialize when no action has run yet; 0 before
        initialization. The registry's idle monitor drains lazy script
        contractors on this clock.
        """
        if self._initialized_at is None:
            return 0.0
        since = (
            self._last_action_at
            if self._last_action_at is not None
            else self._initialized_at
        )
        return time.monotonic() - since

    async def initialize(self) -> None:
        """Subscribe the rules' event types (deduped), then run boot rules
        and the unread-mail rescue scan.

        Boot is asynchronous by design: requests are published and
        initialize() returns without waiting for the agents to come up —
        the registry executes them in background tasks. The unread scan
        replays mail that arrived while the member slept (the mailbox is
        the durable queue), running matching rules on each with a
        synthesized payload.
        """
        event_types = {rule.on for rule in self._spec.rules if rule.on is not None}
        for event_type in sorted(event_types, key=str):
            self.subscribe(event_type, self._on_event)
        self._initialized = True
        self._initialized_at = time.monotonic()
        boot_rules = [rule for rule in self._spec.rules if rule.on is None]
        for rule in boot_rules:
            await self._run_rule(rule, trigger_event=None)
        logger.info(
            "script_member_initialized",
            member_id=self._member_id,
            subscriptions=sorted(event_type.value for event_type in event_types),
            boot_rules=[rule.name for rule in boot_rules],
        )
        await self._scan_unread_mail()

    async def shutdown(self) -> None:
        """Shutdown the member and release bus subscriptions."""
        self.unsubscribe_all()
        self._initialized = False
        self._initialized_at = None
        logger.info("script_member_shutdown", member_id=self._member_id)

    async def _on_event(self, event: Event) -> None:
        """Dispatch one bus event to every rule subscribed to its type.

        Mail sent by the member itself is ignored (own-mail guard, aligned
        with ``Agent._enqueue_mail``); a mail delivered to this member is
        marked read once the rules have seen it.
        """
        mail = event.payload.get("mail") if isinstance(event.payload, dict) else None
        is_own_mail = (
            event.type is EventType.MAIL_DELIVERED
            and isinstance(mail, Mail)
            and mail.from_address == self.address
        )
        if not is_own_mail:
            for rule in self._spec.rules:
                if rule.on is not None and rule.on == event.type:
                    await self._run_rule(rule, trigger_event=event)
        if (
            event.type is EventType.MAIL_DELIVERED
            and isinstance(mail, Mail)
            and event.payload.get("recipient") == self._member_id
        ):
            await self._mark_read(mail)

    async def _scan_unread_mail(self) -> None:
        """Replay unread inbox mail through the matching rules (boot scan).

        The mailbox is the durable queue: mail that arrived while the
        member slept sits unread in the inbox. Each unread mail runs the
        mail rules with a synthesized payload, then is marked read —
        failures of the scan itself are logged and skip the scan only.
        """
        if self._mailbox is None:
            return
        try:
            unread = await self._mailbox.unread()
        except Exception as e:
            logger.error(
                "script_mail_unread_scan_failed", member_id=self._member_id, error=str(e)
            )
            return
        for mail in unread:
            if mail.from_address == self.address:
                await self._mark_read(mail)
                continue
            payload = {"mail": mail, "recipient": self._member_id}
            for rule in self._spec.rules:
                if rule.on is EventType.MAIL_DELIVERED:
                    await self._run_rule(rule, trigger_event=None, payload=payload)
            await self._mark_read(mail)
        if unread:
            logger.info(
                "script_mail_unread_scanned",
                member_id=self._member_id,
                count=len(unread),
            )

    async def _mark_read(self, mail: Mail) -> None:
        """Mark a processed mail read; failures are logged only."""
        if self._mailbox is None:
            return
        try:
            await self._mailbox.mark_read(mail.id)
        except Exception as e:
            logger.warning(
                "script_mail_mark_read_failed",
                member_id=self._member_id,
                mail_id=mail.id,
                error=str(e),
            )

    async def _run_rule(
        self,
        rule: ScriptRule,
        trigger_event: Event | None,
        payload: Any = None,
    ) -> None:
        """Evaluate one rule against an event and execute its decisions.

        ``payload`` overrides the environment's ``event`` value; it is used
        by the unread rescue scan, which has no triggering bus event. An
        EvalError (gate or action evaluation) skips the rule with a
        warning — other rules and the bus dispatch are never affected.
        """
        if trigger_event is not None:
            payload = trigger_event.payload
        env: dict[str, Any] = {
            "event": payload,
            "self": {"id": self._member_id},
        }
        try:
            if rule.when is not None and not eval_form(rule.when, env, self._builtins):
                return
            requests = self._evaluate_actions(rule, env)
        except EvalError as e:
            logger.warning(
                "script_rule_eval_failed",
                member_id=self._member_id,
                rule=rule.name,
                error=str(e),
            )
            return
        logger.debug(
            "script_rule_matched",
            member_id=self._member_id,
            rule=rule.name,
            action_count=len(requests),
        )
        for request in requests:
            await self._execute(rule, request, trigger_event, payload)

    def _evaluate_actions(self, rule: ScriptRule, env: dict[str, Any]) -> list[ActionRequest]:
        """Evaluate the rule's action forms; collect the returned requests."""
        requests: list[ActionRequest] = []
        for action in rule.actions:
            result = eval_form(action, env, self._builtins)
            if isinstance(result, ActionRequest):
                requests.append(result)
            elif isinstance(result, list):
                requests.extend(item for item in result if isinstance(item, ActionRequest))
        return requests

    async def _execute(
        self,
        rule: ScriptRule,
        request: ActionRequest,
        trigger_event: Event | None,
        payload: Any = None,
    ) -> None:
        """Execute one action natively: publish its event on the bus.

        ``start`` / ``stop`` become lifecycle request decisions for the
        registry; ``forward`` becomes a new MAIL_NEW built from the
        triggering mail. The trace carrier passes through from the
        triggering event, so the decision joins its trigger's trace tree
        (Adam's wake behavior); the registry takes the carrier from the
        request event for its own spans.
        """
        if request.kind == "forward":
            await self._execute_forward(rule, request, trigger_event, payload)
            return
        if request.kind == "start":
            event_type = EventType.AGENT_START_REQUEST
            event_payload: Any = StartRequest(
                agent_id=request.data["agent_id"], reason=request.data["reason"]
            )
        elif request.kind == "stop":
            event_type = EventType.AGENT_STOP_REQUEST
            event_payload = StopRequest(
                agent_id=request.data["agent_id"], reason=request.data["reason"]
            )
        else:
            logger.warning(
                "script_action_unknown",
                member_id=self._member_id,
                rule=rule.name,
                action=request.kind,
            )
            return
        logger.info(
            "script_action_requested",
            member_id=self._member_id,
            rule=rule.name,
            action=request.kind,
            agent_id=request.data["agent_id"],
            reason=request.data["reason"],
        )
        self._last_action_at = time.monotonic()
        await self.publish(
            Event(
                type=event_type,
                payload=event_payload,
                source=self.member_id,
                trace_id=trigger_event.trace_id if trigger_event else None,
                span_id=trigger_event.span_id if trigger_event else None,
                parent_span_id=trigger_event.parent_span_id if trigger_event else None,
            )
        )

    async def _execute_forward(
        self,
        rule: ScriptRule,
        request: ActionRequest,
        trigger_event: Event | None,
        payload: Any,
    ) -> None:
        """Forward the triggering mail: build a new Mail and publish MAIL_NEW.

        The new mail is from the member's own address, carries the
        original content, and links back via ``forward_of``. A trigger
        without a mail (a boot rule, a non-mail event) skips with a
        warning; a malformed recipient in the spec fails the same way.
        """
        mail = payload.get("mail") if isinstance(payload, dict) else None
        if not isinstance(mail, Mail):
            logger.warning(
                "script_forward_without_mail",
                member_id=self._member_id,
                rule=rule.name,
            )
            return
        try:
            forwarded = Mail.create(
                from_address=self.address,
                to=request.data["to"],
                cc=request.data["cc"],
                subject=request.data["subject"],
                content=mail.content,
                forward_of=mail.id,
            )
        except ValueError as e:
            logger.warning(
                "script_forward_invalid",
                member_id=self._member_id,
                rule=rule.name,
                error=str(e),
            )
            return
        logger.info(
            "script_action_requested",
            member_id=self._member_id,
            rule=rule.name,
            action="forward",
            mail_id=forwarded.id,
            forward_of=mail.id,
            to=forwarded.to,
        )
        self._last_action_at = time.monotonic()
        await self.publish(
            Event(
                type=EventType.MAIL_NEW,
                payload=forwarded,
                source=self.member_id,
                trace_id=trigger_event.trace_id if trigger_event else None,
                span_id=trigger_event.span_id if trigger_event else None,
                parent_span_id=trigger_event.parent_span_id if trigger_event else None,
            )
        )
