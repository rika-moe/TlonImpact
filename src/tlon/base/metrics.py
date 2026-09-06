"""Metrics pillar for Tlon observability.

Completes the three pillars (traces / logs / metrics) on top of the
OpenTelemetry SDK already used for tracing. ``init_metrics`` builds the
process-wide MeterProvider with an InMemoryMetricReader so the current
values can be snapshotted in-process (REST / operator); attaching an OTLP
metric exporter later needs no code change here.

Metrics are derived, not instrumented: the MetricsService is a pure event
consumer. It subscribes to the EventBus and maps existing events onto
counters and one histogram — business code stays free of metrics calls.

Cardinality discipline: attribute values must stay low-cardinality
(agent id, tool name, recipient kind, event source). Never put mail ids,
session ids or trace ids into attributes.
"""

from typing import Any

from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    Gauge,
    Histogram,
    InMemoryMetricReader,
    Sum,
)

from tlon.base.component import Component
from tlon.base.device import Device
from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus
from tlon.base.logger import get_logger

logger = get_logger(__name__)

# Source of truth: tlon.organization.mail.mail (duplicated here to keep the
# base layer free of upward dependencies).
_POSTMASTER_ADDRESS = "postmaster@tlon.internal"
_HUMAN_ID_PREFIX = "human:"

_initialized = False
_reader: InMemoryMetricReader | None = None


def init_metrics() -> MeterProvider:
    """Initialize the global MeterProvider (idempotent).

    Repeated calls return the existing provider without attaching
    additional metric readers.

    Returns:
        The configured MeterProvider
    """
    global _initialized, _reader
    if _initialized:
        logger.debug("metrics_already_initialized")
        return metrics.get_meter_provider()  # type: ignore[return-value]

    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    metrics.set_meter_provider(provider)
    _reader = reader
    _initialized = True
    logger.info("metrics_initialized")
    return provider


def get_metric_reader() -> InMemoryMetricReader | None:
    """Return the in-memory reader installed by init_metrics (or None)."""
    return _reader


def shutdown_metrics() -> None:
    """Shutdown the global MeterProvider, flushing pending measurements.

    Safe to call when metrics were never initialized (no-op). Also resets
    the idempotency flag so tests can re-initialize afterwards.
    """
    global _initialized, _reader
    if not _initialized:
        return
    provider = metrics.get_meter_provider()
    if hasattr(provider, "shutdown"):
        provider.shutdown()
    _reader = None
    _initialized = False
    logger.info("metrics_shutdown")


def _payload_value(payload: Any, key: str) -> Any:
    """Read a field from an event payload (dict or dataclass-like object)."""
    if isinstance(payload, dict):
        return payload.get(key)
    return getattr(payload, key, None)


class MetricsService(Component, Device):
    """Maps EventBus events onto OpenTelemetry metrics.

    Subscribes in initialize(), unsubscribes in shutdown(). Every handler
    is wrapped so a metrics failure can never break bus dispatch.

    Event -> metric mapping:
        MAIL_NEW            -> tlon.mail.sent {agent=source}
                               (+ tlon.mail.bounced when from postmaster)
        MAIL_DELIVERED      -> tlon.mail.delivered {kind=agent|human}
        AGENT_STARTED       -> tlon.agent.started {agent_id}
        AGENT_WOKEN         -> tlon.agent.woken {agent_id}
        AGENT_SLEEPING      -> tlon.agent.sleeping {agent_id}
        AGENT_STOPPED       -> tlon.agent.stopped {agent_id}
        AGENT_LLM_RESPONSE  -> tlon.llm.calls {agent_id}
                               + tlon.llm.duration_ms histogram {agent_id}
        TOOL_EXECUTE_END    -> tlon.tool.executions {tool_name}
        TOOL_ERROR          -> tlon.tool.errors {tool_name}
        AGENT_NOISY         -> tlon.agent.noisy {agent_id}
        MAIL_ORPHAN_SCAN    -> tlon.mail.orphan_scans
        MAIL_ENDORSED       -> tlon.mail.endorsements {endorser}

    Dependencies (constructor injected):
        - event_bus: EventBus to subscribe to
        - reader: InMemoryMetricReader to snapshot from (defaults to the
          one installed by init_metrics)
    """

    def __init__(
        self,
        event_bus: EventBus,
        reader: InMemoryMetricReader | None = None,
    ):
        """Initialize the metrics service.

        Args:
            event_bus: Event bus to subscribe to
            reader: In-memory reader for snapshots; falls back to the
                module reader installed by init_metrics
        """
        Component.__init__(self, name="MetricsService")
        Device.__init__(self, event_bus=event_bus, name="MetricsService")
        self._reader = reader if reader is not None else get_metric_reader()

        meter = metrics.get_meter("tlon")
        self._mail_sent = meter.create_counter(
            "tlon.mail.sent", description="Mails published as MAIL_NEW"
        )
        self._mail_delivered = meter.create_counter(
            "tlon.mail.delivered",
            description="Per-recipient mail deliveries (kind=agent|human)",
        )
        self._mail_bounced = meter.create_counter(
            "tlon.mail.bounced", description="Bounce mails sent by postmaster"
        )
        self._agent_started = meter.create_counter(
            "tlon.agent.started", description="Agent starts"
        )
        self._agent_woken = meter.create_counter(
            "tlon.agent.woken", description="Agent wakes from sleep"
        )
        self._agent_sleeping = meter.create_counter(
            "tlon.agent.sleeping", description="Agent idle-sleeps"
        )
        self._agent_stopped = meter.create_counter(
            "tlon.agent.stopped", description="Agent stops"
        )
        self._llm_calls = meter.create_counter(
            "tlon.llm.calls", description="Completed LLM calls"
        )
        self._llm_duration = meter.create_histogram(
            "tlon.llm.duration_ms",
            description="LLM call duration",
            unit="ms",
        )
        self._tool_executions = meter.create_counter(
            "tlon.tool.executions", description="Tool executions finished"
        )
        self._tool_errors = meter.create_counter(
            "tlon.tool.errors", description="Tool executions failed"
        )
        self._agent_noisy = meter.create_counter(
            "tlon.agent.noisy", description="AGENT_NOISY public-health facts"
        )
        self._mail_orphan_scans = meter.create_counter(
            "tlon.mail.orphan_scans", description="Orphan-mail scans completed"
        )
        self._mail_endorsements = meter.create_counter(
            "tlon.mail.endorsements", description="Mail endorsements delivered ()"
        )

        logger.info("metrics_service_constructed")

    async def initialize(self) -> None:
        """Start the service by subscribing to the mapped event types."""
        self.subscribe(EventType.MAIL_NEW, self._on_mail_new)
        self.subscribe(EventType.MAIL_DELIVERED, self._on_mail_delivered)
        self.subscribe(EventType.AGENT_STARTED, self._on_agent_started)
        self.subscribe(EventType.AGENT_WOKEN, self._on_agent_woken)
        self.subscribe(EventType.AGENT_SLEEPING, self._on_agent_sleeping)
        self.subscribe(EventType.AGENT_STOPPED, self._on_agent_stopped)
        self.subscribe(EventType.AGENT_LLM_RESPONSE, self._on_llm_response)
        self.subscribe(EventType.TOOL_EXECUTE_END, self._on_tool_execute_end)
        self.subscribe(EventType.TOOL_ERROR, self._on_tool_error)
        self.subscribe(EventType.AGENT_NOISY, self._on_agent_noisy)
        self.subscribe(EventType.MAIL_ORPHAN_SCAN, self._on_mail_orphan_scan)
        self.subscribe(EventType.MAIL_ENDORSED, self._on_mail_endorsed)
        self._initialized = True
        logger.info("metrics_service_initialized")

    async def shutdown(self) -> None:
        """Stop the service by unsubscribing from all events."""
        self.unsubscribe_all()
        self._initialized = False
        logger.info("metrics_service_shutdown")

    # ========== Event Handlers (sync; the bus isolates failures too) ==========

    def _on_mail_new(self, event: Event) -> None:
        """Count sent mail; postmaster bounces count separately."""
        try:
            self._mail_sent.add(1, {"agent": event.source})
            from_address = _payload_value(event.payload, "from_address")
            if from_address == _POSTMASTER_ADDRESS:
                self._mail_bounced.add(1, {})
        except Exception as e:
            logger.warning("metrics_event_failed", event_type=event.type.value, error=str(e))

    def _on_mail_delivered(self, event: Event) -> None:
        """Count one delivery per recipient, tagged by member kind."""
        try:
            recipient = str(_payload_value(event.payload, "recipient") or "")
            kind = "human" if recipient.startswith(_HUMAN_ID_PREFIX) else "agent"
            self._mail_delivered.add(1, {"kind": kind})
        except Exception as e:
            logger.warning("metrics_event_failed", event_type=event.type.value, error=str(e))

    def _on_agent_started(self, event: Event) -> None:
        self._count_agent_event(self._agent_started, event)

    def _on_agent_woken(self, event: Event) -> None:
        self._count_agent_event(self._agent_woken, event)

    def _on_agent_sleeping(self, event: Event) -> None:
        self._count_agent_event(self._agent_sleeping, event)

    def _on_agent_stopped(self, event: Event) -> None:
        self._count_agent_event(self._agent_stopped, event)

    def _count_agent_event(self, counter: Any, event: Event) -> None:
        """Increment an agent lifecycle counter tagged by agent_id."""
        try:
            agent_id = str(_payload_value(event.payload, "agent_id") or "unknown")
            counter.add(1, {"agent_id": agent_id})
        except Exception as e:
            logger.warning("metrics_event_failed", event_type=event.type.value, error=str(e))

    def _on_llm_response(self, event: Event) -> None:
        """Count the LLM call and record its duration."""
        try:
            agent_id = str(_payload_value(event.payload, "agent_id") or "unknown")
            attributes = {"agent_id": agent_id}
            self._llm_calls.add(1, attributes)
            duration_ms = _payload_value(event.payload, "duration_ms")
            if isinstance(duration_ms, (int, float)):
                self._llm_duration.record(float(duration_ms), attributes)
        except Exception as e:
            logger.warning("metrics_event_failed", event_type=event.type.value, error=str(e))

    def _on_tool_execute_end(self, event: Event) -> None:
        try:
            tool_name = str(_payload_value(event.payload, "tool_name") or "unknown")
            self._tool_executions.add(1, {"tool_name": tool_name})
        except Exception as e:
            logger.warning("metrics_event_failed", event_type=event.type.value, error=str(e))

    def _on_tool_error(self, event: Event) -> None:
        try:
            tool_name = str(_payload_value(event.payload, "tool_name") or "unknown")
            self._tool_errors.add(1, {"tool_name": tool_name})
        except Exception as e:
            logger.warning("metrics_event_failed", event_type=event.type.value, error=str(e))

    def _on_agent_noisy(self, event: Event) -> None:
        """Count the public-health noise fact, tagged by agent_id."""
        self._count_agent_event(self._agent_noisy, event)

    def _on_mail_orphan_scan(self, event: Event) -> None:
        """Count one completed orphan scan (orphan_count stays in the payload)."""
        try:
            self._mail_orphan_scans.add(1, {})
        except Exception as e:
            logger.warning("metrics_event_failed", event_type=event.type.value, error=str(e))

    def _on_mail_endorsed(self, event: Event) -> None:
        """Count one endorsement fact, tagged by endorser ()."""
        try:
            endorser = str(_payload_value(event.payload, "endorser") or "unknown")
            self._mail_endorsements.add(1, {"endorser": endorser})
        except Exception as e:
            logger.warning("metrics_event_failed", event_type=event.type.value, error=str(e))

    # ========== Snapshot ==========

    def snapshot(self) -> dict[str, Any]:
        """Collect the current values of all metrics as a JSON-safe dict.

        Returns:
            ``{"metrics": {name: entry}}`` where each entry is
            ``{"type": "counter"|"histogram"|"gauge", "description": str,
            "unit": str, "points": [...]}``. Counter/gauge points carry
            ``{"attributes": {...}, "value": number}``; histogram points
            carry ``{"attributes": {...}, "count": int, "sum": float,
            "min": float, "max": float}``.
        """
        if self._reader is None:
            return {"metrics": {}}

        result: dict[str, Any] = {}
        data = self._reader.get_metrics_data()
        if data is None:
            return {"metrics": result}
        for resource_metrics in data.resource_metrics:
            for scope_metrics in resource_metrics.scope_metrics:
                for metric in scope_metrics.metrics:
                    result[metric.name] = self._metric_entry(metric)
        return {"metrics": result}

    def _metric_entry(self, metric: Any) -> dict[str, Any]:
        """Convert one SDK Metric to its JSON-safe entry."""
        entry: dict[str, Any] = {
            "description": metric.description or "",
            "unit": metric.unit or "",
        }
        data = metric.data
        if isinstance(data, Sum):
            entry["type"] = "counter"
            entry["points"] = [
                {"attributes": dict(p.attributes or {}), "value": p.value}
                for p in data.data_points
            ]
        elif isinstance(data, Histogram):
            entry["type"] = "histogram"
            entry["points"] = [
                {
                    "attributes": dict(p.attributes or {}),
                    "count": p.count,
                    "sum": p.sum,
                    "min": getattr(p, "min", None),
                    "max": getattr(p, "max", None),
                }
                for p in data.data_points
            ]
        elif isinstance(data, Gauge):
            entry["type"] = "gauge"
            entry["points"] = [
                {"attributes": dict(p.attributes or {}), "value": p.value}
                for p in data.data_points
            ]
        else:
            entry["type"] = "unknown"
            entry["points"] = []
        return entry
