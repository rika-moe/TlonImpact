"""Tests for the metrics pillar (tlon.base.metrics).

Covers:
a) init_metrics / shutdown_metrics idempotency.
b) Event -> metric mapping for every subscribed event type.
c) Cardinality discipline (only low-cardinality attribute keys/values).
d) Exception isolation: a failing instrument never breaks the handler.
e) snapshot() structure (counters and the duration histogram).
f) REST /metrics endpoint: 200 with a service, 503 without.
"""

import json
from typing import Any
from unittest.mock import MagicMock

import opentelemetry.metrics._internal as metrics_internal
import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from tlon.app.server.api.rest import RESTAPIHandler
from tlon.base import metrics as metrics_module
from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus
from tlon.base.metrics import MetricsService
from tlon.organization.mail.mail import POSTMASTER_ADDRESS, Mail

ALLOWED_ATTRIBUTE_KEYS = {"agent", "kind", "agent_id", "tool_name"}


@pytest.fixture
def metric_setup(monkeypatch: pytest.MonkeyPatch) -> tuple[InMemoryMetricReader, MetricsService]:
    """Install an isolated MeterProvider and build a MetricsService on it."""
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    # Bypass OTel's one-time set_meter_provider guard (same pattern as
    # the tracing tests with trace._TRACER_PROVIDER)
    monkeypatch.setattr(metrics_internal, "_METER_PROVIDER", provider)
    service = MetricsService(event_bus=EventBus(), reader=reader)
    return reader, service


def _make_mail(from_address: str = "gu-yanxi@tlon.internal") -> Mail:
    return Mail.create(
        from_address=from_address,
        to=["xie-yunshu@tlon.internal"],
        content="hello",
    )


def _point(snapshot: dict[str, Any], name: str, **attrs: str) -> dict[str, Any] | None:
    """Find the single data point of `name` whose attributes match `attrs`."""
    for point in snapshot["metrics"][name]["points"]:
        if all(point["attributes"].get(k) == v for k, v in attrs.items()):
            return point
    return None


# ── init / shutdown ──


def test_init_metrics_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """init_metrics installs one provider + reader; repeated calls reuse them."""
    monkeypatch.setattr(metrics_module, "_initialized", False)
    monkeypatch.setattr(metrics_module, "_reader", None)
    try:
        provider = metrics_module.init_metrics()
        reader = metrics_module.get_metric_reader()
        assert isinstance(provider, MeterProvider)
        assert reader is not None
        assert metrics_module.init_metrics() is provider
        assert metrics_module.get_metric_reader() is reader
    finally:
        metrics_module.shutdown_metrics()
        # shutdown_metrics resets the flags but the OTel global provider
        # stays; restore isolation for other tests
        monkeypatch.setattr(metrics_module, "_initialized", False)
        monkeypatch.setattr(metrics_module, "_reader", None)


# ── Event -> metric mapping ──


async def test_mail_new_counts_sent_and_bounce(
    metric_setup: tuple[InMemoryMetricReader, MetricsService],
) -> None:
    """MAIL_NEW counts tlon.mail.sent by source; postmaster mail also bounces."""
    _, service = metric_setup
    service._on_mail_new(Event(type=EventType.MAIL_NEW, payload=_make_mail(), source="gu-yanxi"))
    service._on_mail_new(
        Event(type=EventType.MAIL_NEW, payload=_make_mail(POSTMASTER_ADDRESS), source="mail_delivery")
    )

    snapshot = service.snapshot()
    assert _point(snapshot, "tlon.mail.sent", agent="gu-yanxi")["value"] == 1
    assert _point(snapshot, "tlon.mail.sent", agent="mail_delivery")["value"] == 1
    bounced = snapshot["metrics"]["tlon.mail.bounced"]["points"]
    assert sum(p["value"] for p in bounced) == 1


async def test_mail_delivered_counts_by_member_kind(
    metric_setup: tuple[InMemoryMetricReader, MetricsService],
) -> None:
    """MAIL_DELIVERED tags the recipient kind (agent|human), never the full id."""
    _, service = metric_setup
    mail = _make_mail()
    service._on_mail_delivered(
        Event(
            type=EventType.MAIL_DELIVERED,
            payload={"mail": mail, "recipient": "xie-yunshu"},
            source="mail_delivery",
        )
    )
    service._on_mail_delivered(
        Event(
            type=EventType.MAIL_DELIVERED,
            payload={"mail": mail, "recipient": "human:alice"},
            source="mail_delivery",
        )
    )

    snapshot = service.snapshot()
    assert _point(snapshot, "tlon.mail.delivered", kind="agent")["value"] == 1
    assert _point(snapshot, "tlon.mail.delivered", kind="human")["value"] == 1


async def test_agent_lifecycle_counters(
    metric_setup: tuple[InMemoryMetricReader, MetricsService],
) -> None:
    """AGENT_STARTED/WOKEN/SLEEPING/STOPPED each increment their counter."""
    _, service = metric_setup
    cases = [
        (EventType.AGENT_STARTED, service._on_agent_started, "tlon.agent.started"),
        (EventType.AGENT_WOKEN, service._on_agent_woken, "tlon.agent.woken"),
        (EventType.AGENT_SLEEPING, service._on_agent_sleeping, "tlon.agent.sleeping"),
        (EventType.AGENT_STOPPED, service._on_agent_stopped, "tlon.agent.stopped"),
    ]
    for event_type, handler, _ in cases:
        handler(Event(type=event_type, payload={"agent_id": "gu-yanxi"}, source="agent_registry"))

    snapshot = service.snapshot()
    for _, _, metric_name in cases:
        assert _point(snapshot, metric_name, agent_id="gu-yanxi")["value"] == 1


async def test_llm_response_counts_call_and_records_duration(
    metric_setup: tuple[InMemoryMetricReader, MetricsService],
) -> None:
    """AGENT_LLM_RESPONSE increments tlon.llm.calls and records the histogram."""
    _, service = metric_setup
    payload = {"agent_id": "gu-yanxi", "model": "m", "duration_ms": 120}
    service._on_llm_response(Event(type=EventType.AGENT_LLM_RESPONSE, payload=payload, source="s"))
    service._on_llm_response(
        Event(
            type=EventType.AGENT_LLM_RESPONSE,
            payload={**payload, "duration_ms": 80},
            source="s",
        )
    )

    snapshot = service.snapshot()
    assert _point(snapshot, "tlon.llm.calls", agent_id="gu-yanxi")["value"] == 2
    duration = _point(snapshot, "tlon.llm.duration_ms", agent_id="gu-yanxi")
    assert duration["count"] == 2
    assert duration["sum"] == 200.0
    assert duration["min"] == 80.0
    assert duration["max"] == 120.0


async def test_tool_counters(
    metric_setup: tuple[InMemoryMetricReader, MetricsService],
) -> None:
    """TOOL_EXECUTE_END / TOOL_ERROR count by tool_name."""
    _, service = metric_setup
    service._on_tool_execute_end(
        Event(
            type=EventType.TOOL_EXECUTE_END,
            payload={"agent_id": "a", "tool_name": "sendEmail", "success": True},
            source="a",
        )
    )
    service._on_tool_error(
        Event(
            type=EventType.TOOL_ERROR,
            payload={"agent_id": "a", "tool_name": "sendEmail", "error": "boom"},
            source="a",
        )
    )

    snapshot = service.snapshot()
    assert _point(snapshot, "tlon.tool.executions", tool_name="sendEmail")["value"] == 1
    assert _point(snapshot, "tlon.tool.errors", tool_name="sendEmail")["value"] == 1


# ── Cardinality discipline ──


async def test_no_high_cardinality_attributes(
    metric_setup: tuple[InMemoryMetricReader, MetricsService],
) -> None:
    """No mail id / session id / trace id leaks into metric attributes."""
    _, service = metric_setup
    mail = _make_mail()
    service._on_mail_new(Event(type=EventType.MAIL_NEW, payload=mail, source="gu-yanxi"))
    service._on_mail_delivered(
        Event(
            type=EventType.MAIL_DELIVERED,
            payload={"mail": mail, "recipient": "human:alice"},
            source="mail_delivery",
        )
    )
    service._on_llm_response(
        Event(
            type=EventType.AGENT_LLM_RESPONSE,
            payload={"agent_id": "gu-yanxi", "duration_ms": 5},
            source="s",
        )
    )

    snapshot = service.snapshot()
    for entry in snapshot["metrics"].values():
        for point in entry["points"]:
            assert set(point["attributes"]) <= ALLOWED_ATTRIBUTE_KEYS
            assert mail.id not in point["attributes"].values()
            assert "human:alice" not in point["attributes"].values()


# ── Exception isolation ──


async def test_handler_failure_does_not_propagate(
    metric_setup: tuple[InMemoryMetricReader, MetricsService],
) -> None:
    """A raising instrument is swallowed (warning logged), the handler returns."""
    _, service = metric_setup
    broken = MagicMock()
    broken.add.side_effect = RuntimeError("instrument exploded")
    service._tool_errors = broken

    event = Event(
        type=EventType.TOOL_ERROR,
        payload={"agent_id": "a", "tool_name": "sendEmail"},
        source="a",
    )
    service._on_tool_error(event)  # must not raise


async def test_malformed_payloads_do_not_raise(
    metric_setup: tuple[InMemoryMetricReader, MetricsService],
) -> None:
    """None / wrong-shaped payloads degrade to 'unknown' attributes, no crash."""
    _, service = metric_setup
    service._on_agent_started(Event(type=EventType.AGENT_STARTED, payload=None, source="s"))
    service._on_llm_response(Event(type=EventType.AGENT_LLM_RESPONSE, payload=None, source="s"))
    service._on_mail_delivered(Event(type=EventType.MAIL_DELIVERED, payload=None, source="s"))

    snapshot = service.snapshot()
    assert _point(snapshot, "tlon.agent.started", agent_id="unknown")["value"] == 1
    # A None payload has no duration_ms: the call counts, the histogram doesn't
    assert _point(snapshot, "tlon.llm.calls", agent_id="unknown")["value"] == 1
    assert "tlon.llm.duration_ms" not in snapshot["metrics"]
    assert _point(snapshot, "tlon.mail.delivered", kind="agent")["value"] == 1


# ── Snapshot structure ──


async def test_snapshot_structure(
    metric_setup: tuple[InMemoryMetricReader, MetricsService],
) -> None:
    """snapshot() is JSON-serializable with the documented entry shape."""
    _, service = metric_setup
    service._on_mail_new(Event(type=EventType.MAIL_NEW, payload=_make_mail(), source="gu-yanxi"))
    service._on_llm_response(
        Event(
            type=EventType.AGENT_LLM_RESPONSE,
            payload={"agent_id": "gu-yanxi", "duration_ms": 42},
            source="s",
        )
    )

    snapshot = service.snapshot()
    json.dumps(snapshot)  # must not raise

    counter = snapshot["metrics"]["tlon.mail.sent"]
    assert counter["type"] == "counter"
    assert {"attributes", "value"} <= set(counter["points"][0])

    histogram = snapshot["metrics"]["tlon.llm.duration_ms"]
    assert histogram["type"] == "histogram"
    assert histogram["unit"] == "ms"
    assert {"attributes", "count", "sum", "min", "max"} <= set(histogram["points"][0])


async def test_snapshot_without_reader_is_empty() -> None:
    """A service without a reader snapshots to an empty metrics dict."""
    service = MetricsService(event_bus=EventBus(), reader=InMemoryMetricReader())
    service._reader = None
    assert service.snapshot() == {"metrics": {}}


# ── Lifecycle (subscribe / unsubscribe) ──


async def test_initialize_subscribes_and_shutdown_unsubscribes(
    metric_setup: tuple[InMemoryMetricReader, MetricsService],
) -> None:
    """initialize() registers one handler per mapped event type on the bus."""
    _, service = metric_setup
    bus = service.event_bus
    await service.initialize()
    assert len(service._subscriptions) == 9
    for event_type in (
        EventType.MAIL_NEW,
        EventType.MAIL_DELIVERED,
        EventType.AGENT_STARTED,
        EventType.AGENT_WOKEN,
        EventType.AGENT_SLEEPING,
        EventType.AGENT_STOPPED,
        EventType.AGENT_LLM_RESPONSE,
        EventType.TOOL_EXECUTE_END,
        EventType.TOOL_ERROR,
    ):
        assert bus._subscribers.get(event_type), f"missing subscription for {event_type}"
    await service.shutdown()
    assert service._subscriptions == {}
    assert all(not handlers for handlers in bus._subscribers.values())


# ── REST endpoint ──


async def test_rest_metrics_200(
    metric_setup: tuple[InMemoryMetricReader, MetricsService],
) -> None:
    """GET /metrics returns the service snapshot."""
    _, service = metric_setup
    service._on_mail_new(Event(type=EventType.MAIL_NEW, payload=_make_mail(), source="gu-yanxi"))
    handler = RESTAPIHandler(None, None, metrics_service=service)

    response = await handler.handle_metrics(MagicMock())

    assert response.status == 200
    body = json.loads(response.text)
    assert "tlon.mail.sent" in body["metrics"]


async def test_rest_metrics_503_when_unavailable() -> None:
    """GET /metrics returns 503 when no metrics service is injected."""
    handler = RESTAPIHandler(None, None)

    response = await handler.handle_metrics(MagicMock())

    assert response.status == 503
    body = json.loads(response.text)
    assert body["status"] == "error"
