"""StageServices — the service bundle stages may query/await (C1).

Under the effects-as-data split, a stage's external contact divides
into three kinds:

- Read-only external queries (the LLM call): injected services — here.
- Observability (logs, events, trace spans): injected services — here.
- System-state changes: never performed by the stage; declared in the
  returned :class:`~tlon.agents.stages.outcome.StageOutcome` instead.

Everything here is assembled from the Agent's existing injected
dependencies — the Agent itself is not needed.
"""

import contextlib
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from typing import TYPE_CHECKING, Any

from structlog.contextvars import bound_contextvars

from tlon.agents.trigger import AgentIdentity
from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus
from tlon.base.llm_client import LLMClient, LLMResponse, _cache_token_counts
from tlon.base.tracing.trace_context import continue_trace, get_current_trace, start_span

if TYPE_CHECKING:
    import structlog

    from tlon.agents.context_builder import BuiltContext, ContextBuilder
    from tlon.agents.session import AgentSession
    from tlon.organization.mail.mail import Mail

# Matches Device.emit's signature: (event_type, payload, *, source,
# trace_id, span_id, parent_span_id) -> None.
EventPublisher = Callable[..., Awaitable[None]]


def make_event_publisher(event_bus: EventBus, default_source: str) -> EventPublisher:
    """Build a standalone event publisher with Device.emit semantics.

    Closes over the event bus and a default source — not over any Member —
    so services/executors publishing through it hold no reference to the
    Agent.
    """

    async def publish(
        event_type: EventType,
        payload: Any,
        *,
        source: str | None = None,
        trace_id: str | None = None,
        span_id: str | None = None,
        parent_span_id: str | None = None,
    ) -> None:
        await event_bus.publish(
            Event(
                type=event_type,
                payload=payload,
                source=source or default_source,
                trace_id=trace_id,
                span_id=span_id,
                parent_span_id=parent_span_id,
            )
        )

    return publish


# Zero-arg provider of the tool schemas for the LLM call
# (ToolExecutor.get_tool_definitions).
ToolDefinitions = Callable[[], "list[dict[str, Any]]"]


class StageServices:
    """Query/observability services injected into the pure stages."""

    def __init__(
        self,
        identity: AgentIdentity,
        source_hash: str | None,
        llm_client: LLMClient,
        context_builder: "ContextBuilder",
        publish: EventPublisher,
        tool_definitions: ToolDefinitions,
        logger: "structlog.stdlib.BoundLogger",
    ):
        """Assemble the bundle from the Agent's injected dependencies.

        Args:
            identity: Agent identity snapshot (id/name/address)
            source_hash: Provenance hash of the member's behavior file,
                carried on the span attributes for observability
            llm_client: LLM client for inference
            context_builder: System-prompt provider (cached)
            publish: Event-bus publish closure (Device.emit)
            tool_definitions: Tool schema provider for the LLM call
            logger: Per-agent bound logger (agent_id already bound)
        """
        self.identity = identity
        self._source_hash = source_hash
        self.logger = logger
        self._llm_client = llm_client
        self._context_builder = context_builder
        self._publish = publish
        self._tool_definitions = tool_definitions

    @contextlib.asynccontextmanager
    async def traced_span(self, name: str) -> AsyncIterator[Any]:
        """Open a named trace span and yield its span context.

        Persistence is handled by the OTel JSONL exporter; this only
        manages the span lifecycle.
        """
        with start_span(name=name, source=self.identity.id) as ctx:
            yield ctx

    @contextlib.contextmanager
    def session_span(self, session: "AgentSession") -> Iterator[None]:
        """Per-round trace context for a session's advance.

        OTel context is contextvar-based, so it does not survive across
        event-loop turns: each round re-attaches the carrier stored on the
        session and opens an "agent.session" span around that round's work
        (think step or tool-result collection).
        """
        carrier = session.carrier or {}
        trace_id = carrier.get("trace_id")
        with contextlib.ExitStack() as stack:
            # session_id is also bound so tools executing within this round
            # (background tasks inherit contextvars) can attribute their
            # work to the session — e.g. spawnAgent's per-session quota.
            stack.enter_context(bound_contextvars(agent_id=self.identity.id, session_id=session.id))
            if trace_id:
                stack.enter_context(continue_trace(trace_id, carrier.get("span_id")))
            stack.enter_context(
                start_span(
                    "agent.session",
                    source=self.identity.id,
                    attributes={
                        "session_id": session.id,
                        "state": session.state.value,
                        "spec_hash": self._source_hash or "",
                    },
                )
            )
            yield

    async def build_context(
        self, trigger_text: str, responsibility: "list[Mail]"
    ) -> "BuiltContext":
        """Build the LLM conversation context for a session start.

        ADR 0028: the trigger is the fixed wake template (no mail body);
        the responsibility set feeds the JIT knowledge query.
        """
        return await self._context_builder.build_context(trigger_text, responsibility)

    async def publish_event(self, event_type: EventType, payload: Any) -> None:
        """Publish an observability event (source: this agent)."""
        await self._publish(event_type, payload=payload, source=self.identity.id)

    async def _emit_with_trace(self, event_type: EventType, payload: dict[str, Any]) -> None:
        """Publish an observability event with the current trace injected."""
        current_trace = get_current_trace()
        await self._publish(
            event_type,
            payload=payload,
            source=self.identity.id,
            trace_id=current_trace.trace_id if current_trace else None,
            span_id=current_trace.span_id if current_trace else None,
            parent_span_id=current_trace.parent_span_id if current_trace else None,
        )

    def _model_name(self) -> str:
        """The LLM model name for observability payloads."""
        if not hasattr(self._llm_client, "config"):
            return "unknown"
        return self._llm_client.config.model_name or "unknown"

    async def distill(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        *,
        span: str,
        session_id: str | None = None,
    ) -> LLMResponse:
        """One auxiliary LLM call with an explicit system prompt, no tools.

        Unlike :meth:`think` this is not a round of the cognitive loop:
        the caller supplies the prompt and messages outright, and no tool
        schemas are attached (a tool-emitting answer is useless here).
        Used by the engine for the resource-ceiling reply distillation
        (max_iterations / max_tokens, ADR 0023).
        """
        async with self.traced_span(span):
            return await self._llm_client.chat(
                system_prompt=system_prompt,
                messages=messages,
                purpose="distill",
                session_id=session_id,
            )

    async def think(
        self,
        context: list[dict[str, Any]],
        *,
        span: str,
        purpose: str = "think",
        session_id: str | None = None,
        cache_prefix_len: int | None = None,
    ) -> LLMResponse:
        """Call the LLM once — a read-only external query, not an effect.

        Emits the AGENT_THINKING_START / AGENT_LLM_REQUEST /
        AGENT_LLM_RESPONSE / AGENT_THINKING_END observability events
        inside the named span and returns the raw response; any
        session-state consequence is the caller's Outcome.
        ``cache_prefix_len`` declares the stable prefix (the session
        anchor) for cache breakpoint placement on explicit-caching
        providers.
        """
        async with self.traced_span(span):
            system_prompt = await self._context_builder.get_system_prompt()
            tools = self._tool_definitions()

            await self._emit_with_trace(
                EventType.AGENT_THINKING_START,
                {
                    "agent_id": self.identity.id,
                    "agent_name": self.identity.name,
                    "context_length": len(context),
                },
            )
            await self._emit_with_trace(
                EventType.AGENT_LLM_REQUEST,
                {
                    "agent_id": self.identity.id,
                    "model": self._model_name(),
                    "messages_count": len(context),
                    "tools_count": len(tools) if tools else 0,
                },
            )

            start_time = time.time()
            response = await self._llm_client.chat(
                system_prompt=system_prompt,
                messages=context,
                tools=tools if tools else None,
                purpose=purpose,
                session_id=session_id,
                cache_prefix_len=cache_prefix_len,
            )
            duration_ms = int((time.time() - start_time) * 1000)

            usage = response.usage or {}
            prompt_tokens = int(usage.get("prompt_tokens") or 0)
            completion_tokens = int(usage.get("completion_tokens") or 0)
            cached_tokens, cache_write_tokens = _cache_token_counts(usage)

            await self._emit_with_trace(
                EventType.AGENT_LLM_RESPONSE,
                {
                    "agent_id": self.identity.id,
                    "model": self._model_name(),
                    "duration_ms": duration_ms,
                    "finish_reason": response.finish_reason,
                    "has_tool_calls": bool(response.tool_calls),
                    "tool_call_count": len(response.tool_calls) if response.tool_calls else 0,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "cached_tokens": cached_tokens,
                    "cache_write_tokens": cache_write_tokens,
                },
            )
            await self._emit_with_trace(
                EventType.AGENT_THINKING_END,
                {
                    "agent_id": self.identity.id,
                    "agent_name": self.identity.name,
                    "duration_ms": duration_ms,
                },
            )

            return response
