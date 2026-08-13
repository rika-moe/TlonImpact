"""Unit tests for server-side event filtering (NotificationService).

The NotificationService is the push point for system/agent events to
clients. These tests cover:
- Mode semantics (chat / event / agent)
- Single-track mail delivery: chat-mode mail.delivered is never pushed
  (R2) — human mail delivery goes through the HumanAdapter
- Session filters: event_types, exclude_event_types, trace_id, agent
- Session creation with initial filters (SessionManager)
"""

import pytest

from tlon.app.server.core.notification_service import NotificationService
from tlon.app.server.core.session_manager import ClientSession, SessionManager
from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus


def make_session(
    mode: str = "chat",
    filters: dict | None = None,
    client_type: str = "tui",
    metadata: dict | None = None,
) -> ClientSession:
    """Create a ClientSession for filtering tests."""
    return ClientSession(
        id=f"{client_type}-0001",
        client_type=client_type,
        connected_at="2026-01-01T00:00:00",
        mode=mode,
        filters=filters or {},
        metadata=metadata or {},
    )


def make_event(
    event_type: EventType,
    source: str = "dev-001",
    trace_id: str | None = None,
    payload: dict | None = None,
) -> Event:
    """Create an Event for filtering tests."""
    return Event(
        type=event_type,
        payload=payload if payload is not None else {},
        source=source,
        trace_id=trace_id,
    )


class TestShouldSendToSession:
    """Tests for NotificationService._should_send_to_session."""

    @pytest.fixture
    def service(self):
        """Create a NotificationService (no initialize needed for filtering)."""
        return NotificationService(event_bus=EventBus(), session_manager=SessionManager())

    # ==================== Mode semantics ====================

    def test_chat_mode_allows_chat_event_set(self, service):
        """Chat mode: messages, agent lifecycle highlights, system events."""
        session = make_session(mode="chat", metadata={"username": "alice"})

        assert service._should_send_to_session(session, make_event(EventType.MAIL_NEW))
        assert service._should_send_to_session(session, make_event(EventType.AGENT_RESPONSE))
        assert service._should_send_to_session(session, make_event(EventType.AGENT_STARTED))
        assert service._should_send_to_session(session, make_event(EventType.AGENT_STOPPED))
        assert service._should_send_to_session(session, make_event(EventType.AGENT_ERROR))
        assert service._should_send_to_session(session, make_event(EventType.SYSTEM_STARTUP))

    def test_chat_mode_rejects_other_events(self, service):
        """Chat mode rejects detailed agent/tool/trace events."""
        session = make_session(mode="chat")

        assert not service._should_send_to_session(session, make_event(EventType.AGENT_THINKING))
        assert not service._should_send_to_session(session, make_event(EventType.AGENT_JOIN))
        assert not service._should_send_to_session(session, make_event(EventType.TOOL_EXECUTE))
        assert not service._should_send_to_session(session, make_event(EventType.CONFIG_UPDATED))

    def test_agent_mode_allows_only_agent_events(self, service):
        """Agent mode: only agent.* events (including high-frequency ones)."""
        session = make_session(mode="agent")

        assert service._should_send_to_session(session, make_event(EventType.AGENT_THINKING))
        assert service._should_send_to_session(session, make_event(EventType.AGENT_LLM_REQUEST))

        assert not service._should_send_to_session(session, make_event(EventType.MAIL_NEW))
        assert not service._should_send_to_session(session, make_event(EventType.TOOL_EXECUTE))
        assert not service._should_send_to_session(session, make_event(EventType.SYSTEM_STARTUP))

    def test_event_mode_allows_everything(self, service):
        """Event mode imposes no event-type restriction."""
        session = make_session(mode="event")

        for event_type in EventType:
            assert service._should_send_to_session(session, make_event(event_type))

    # ==================== Mail delivery single-track (R2) ====================

    def test_chat_mode_mail_delivered_never_pushed(self, service):
        """Chat mode: mail.delivered never goes through this service.

        Human mail delivery is single-track through the HumanAdapter
        (member layer), so the NotificationService blocks mail.delivered
        for chat sessions regardless of the recipient.
        """
        session = make_session(mode="chat", metadata={"username": "alice"})

        # Even mail addressed to this session's own human is blocked here
        assert not service._should_send_to_session(
            session,
            make_event(EventType.MAIL_DELIVERED, payload={"recipient": "human:alice"}),
        )
        assert not service._should_send_to_session(
            session,
            make_event(EventType.MAIL_DELIVERED, payload={"recipient": "human:other"}),
        )
        assert not service._should_send_to_session(
            session,
            make_event(EventType.MAIL_DELIVERED, payload={"recipient": "gu-yanxi"}),
        )

    def test_event_mode_mail_delivered_not_filtered(self, service):
        """Event mode (operator console) sees all mail traffic."""
        session = make_session(mode="event", metadata={"username": "alice"})

        assert service._should_send_to_session(
            session,
            make_event(EventType.MAIL_DELIVERED, payload={"recipient": "human:other"}),
        )
        assert service._should_send_to_session(
            session,
            make_event(EventType.MAIL_DELIVERED, payload={"recipient": "gu-yanxi"}),
        )

    def test_non_mail_events_unaffected_by_delivery_rule(self, service):
        """The mail.delivered block applies to that event type only."""
        session = make_session(mode="chat", metadata={"username": "alice"})

        # mail.new has no recipient and passes as before
        assert service._should_send_to_session(session, make_event(EventType.MAIL_NEW))
        assert service._should_send_to_session(
            session, make_event(EventType.AGENT_RESPONSE)
        )

    # ==================== Session filters ====================

    def test_trace_id_filter(self, service):
        """trace_id filter matches the event envelope trace_id."""
        session = make_session(mode="event", filters={"trace_id": "trace-abc"})

        assert service._should_send_to_session(
            session, make_event(EventType.AGENT_THINKING, trace_id="trace-abc")
        )
        assert not service._should_send_to_session(
            session, make_event(EventType.AGENT_THINKING, trace_id="trace-other")
        )
        assert not service._should_send_to_session(session, make_event(EventType.AGENT_THINKING))

    def test_trace_id_filter_falls_back_to_payload(self, service):
        """trace_id filter also matches a trace_id inside a dict payload."""
        session = make_session(mode="event", filters={"trace_id": "trace-abc"})

        event = make_event(EventType.MAIL_NEW, payload={"trace_id": "trace-abc"})
        assert service._should_send_to_session(session, event)

    def test_event_types_include_list(self, service):
        """event_types restricts to the listed types."""
        session = make_session(
            mode="event", filters={"event_types": ["mail.new", "agent.response"]}
        )

        assert service._should_send_to_session(session, make_event(EventType.MAIL_NEW))
        assert service._should_send_to_session(session, make_event(EventType.AGENT_RESPONSE))
        assert not service._should_send_to_session(session, make_event(EventType.AGENT_THINKING))
        assert not service._should_send_to_session(session, make_event(EventType.TOOL_EXECUTE))

    def test_event_types_prefix_wildcard(self, service):
        """event_types supports 'prefix.*' wildcards."""
        session = make_session(mode="event", filters={"event_types": ["agent.*"]})

        assert service._should_send_to_session(session, make_event(EventType.AGENT_THINKING))
        assert not service._should_send_to_session(session, make_event(EventType.MAIL_NEW))

    def test_exclude_event_types(self, service):
        """exclude_event_types drops the listed types."""
        session = make_session(
            mode="event",
            filters={"exclude_event_types": ["agent.llm_request", "agent.llm_response"]},
        )

        assert not service._should_send_to_session(session, make_event(EventType.AGENT_LLM_REQUEST))
        assert not service._should_send_to_session(
            session, make_event(EventType.AGENT_LLM_RESPONSE)
        )
        assert service._should_send_to_session(session, make_event(EventType.MAIL_NEW))
        assert service._should_send_to_session(session, make_event(EventType.AGENT_THINKING))

    def test_agent_source_filter(self, service):
        """The pre-existing 'agent' source pattern filter still works."""
        session = make_session(mode="event", filters={"agent": "dev-*"})

        assert service._should_send_to_session(
            session, make_event(EventType.MAIL_NEW, source="dev-001")
        )
        assert not service._should_send_to_session(
            session, make_event(EventType.MAIL_NEW, source="pm-001")
        )

    def test_filters_combine_with_mode(self, service):
        """Filters and mode semantics are ANDed."""
        session = make_session(mode="chat", filters={"event_types": ["agent.thinking"]})

        # agent.thinking passes the event_types filter but not chat mode
        assert not service._should_send_to_session(session, make_event(EventType.AGENT_THINKING))
        # mail.new passes chat mode but not the event_types filter
        assert not service._should_send_to_session(session, make_event(EventType.MAIL_NEW))

    def test_filters_combine_with_each_other(self, service):
        """Multiple filter dimensions are ANDed."""
        session = make_session(
            mode="event",
            filters={"event_types": ["agent.*"], "exclude_event_types": ["agent.llm_request"]},
        )

        assert service._should_send_to_session(session, make_event(EventType.AGENT_THINKING))
        assert not service._should_send_to_session(session, make_event(EventType.AGENT_LLM_REQUEST))
        assert not service._should_send_to_session(session, make_event(EventType.MAIL_NEW))


class TestSessionManagerFilters:
    """Tests for session creation with initial filters."""

    @pytest.mark.asyncio
    async def test_create_session_with_filters(self):
        """Initial filters declared at connect time are stored on the session."""
        manager = SessionManager()
        filters = {"exclude_event_types": ["agent.llm_request"]}

        session = await manager.create_session("web", filters=filters)

        assert session.filters == filters
        # The session must own a copy, not the caller's dict
        assert session.filters is not filters

    @pytest.mark.asyncio
    async def test_create_session_without_filters(self):
        """Sessions default to empty filters."""
        manager = SessionManager()
        session = await manager.create_session("tui")
        assert session.filters == {}

    @pytest.mark.asyncio
    async def test_update_session_filters_replaces_wholesale(self):
        """update_session_filters replaces the filter dict entirely."""
        manager = SessionManager()
        session = await manager.create_session("tui", filters={"trace_id": "t-1"})

        assert manager.update_session_filters(session.id, {"event_types": ["mail.new"]})
        assert session.filters == {"event_types": ["mail.new"]}
