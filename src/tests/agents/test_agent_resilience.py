"""Tests for Agent resilience configuration.

Covers the four behavior fixes from the agent.py split:
- AgentConfig.max_iterations feeds DefaultSpec (spec.loop is the runtime
  authority for the ReAct iteration limit)
- AgentConfig.session_timeout_seconds abandons stuck sessions
- AgentConfig.message_queue_size bounds the inbound queue (drop, never block)
- AgentConfig.max_consecutive_errors abandons a session after N LLM errors
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from tlon.agents.agent import Agent, AgentState
from tlon.agents.tool_executor import ToolExecutor
from tlon.base.config import AgentConfig
from tlon.base.events.event import Event, EventType
from tlon.base.llm_client import LLMResponse, ToolCall
from tlon.organization.mail.mail import Mail
from tlon.tools.base import ToolResult


def _make_agent(config: AgentConfig | None = None, **kwargs):
    """Build an Agent with all deps as mocks."""
    event_bus = MagicMock()
    event_bus.publish = AsyncMock()
    event_bus.subscribe = MagicMock(return_value="sub-id")
    event_bus.unsubscribe = MagicMock()

    defaults = {
        "config": config or AgentConfig(id="a-001", name="TestAgent"),
        "event_bus": event_bus,
        "llm_client": MagicMock(),
        "memory": AsyncMock(),
        "task_manager": MagicMock(),
        "tool_executor": ToolExecutor(),
    }
    defaults.update(kwargs)
    return Agent(**defaults)


def _make_message(content: str = "Hello") -> Mail:
    return Mail.create(
        from_address="user-001@tlon.internal",
        to=["a-001@tlon.internal"],
        content=content,
    )


class TestResilienceConfigDefaults:
    def test_defaults(self):
        config = AgentConfig(id="a-001", name="TestAgent")
        assert config.max_iterations == 10
        assert config.max_consecutive_errors == 3
        assert config.session_timeout_seconds == 1800
        assert config.message_queue_size == 100

    def test_message_queue_bounded_by_config(self):
        agent = _make_agent(
            config=AgentConfig(id="a-001", name="TestAgent", message_queue_size=7)
        )
        assert agent._mail_queue.maxsize == 7


class TestConfigurableMaxIterations:
    async def test_max_iterations_from_config(self):
        """A low max_iterations makes the loop give up sooner."""
        config = AgentConfig(id="a-001", name="TestAgent", max_iterations=2)
        llm = MagicMock()
        llm.chat = AsyncMock(
            return_value=LLMResponse(
                content=None,
                tool_calls=[ToolCall(id="tc-1", function_name="testTool", arguments={})],
            )
        )
        agent = _make_agent(config=config, llm_client=llm)

        mock_tool = MagicMock()
        mock_tool.name = "testTool"
        mock_tool.execute = AsyncMock(return_value=ToolResult(success=True, data={}))
        agent.tool_executor.register_tool(mock_tool)

        await agent._process_message(_make_message())

        # 2 think steps, then the iteration limit fires
        assert llm.chat.call_count == 2
        calls = agent.event_bus.publish.await_args_list
        message_events = [c for c in calls if c.args[0].type == EventType.MAIL_NEW]
        assert len(message_events) == 1
        assert "简化" in message_events[0].args[0].payload.content


class TestConfigurableMaxConsecutiveErrors:
    async def test_gives_up_after_configured_errors(self):
        """max_consecutive_errors=1 abandons the session on the first LLM error."""
        config = AgentConfig(id="a-001", name="TestAgent", max_consecutive_errors=1)
        llm = MagicMock()
        llm.chat = AsyncMock(side_effect=Exception("Persistent failure"))
        agent = _make_agent(config=config, llm_client=llm)

        await agent._process_message(_make_message())

        assert llm.chat.call_count == 1
        calls = agent.event_bus.publish.await_args_list
        message_events = [c for c in calls if c.args[0].type == EventType.MAIL_NEW]
        assert len(message_events) == 1
        assert "错误" in message_events[0].args[0].payload.content


class TestMessageQueueBackpressure:
    async def test_full_queue_drops_without_blocking(self):
        """A full inbound queue drops new messages instead of blocking dispatch."""
        config = AgentConfig(id="a-001", name="TestAgent", message_queue_size=2)
        agent = _make_agent(config=config)

        def make_event() -> Event:
            mail = Mail.create(
                from_address="user-001@tlon.internal",
                to=["a-001@tlon.internal"],
                content="hi",
            )
            return Event(
                type=EventType.MAIL_DELIVERED,
                payload={"mail": mail, "recipient": "a-001"},
                source="mail_delivery",
            )

        # 4 mails against a queue of capacity 2; dispatch must not block
        for _ in range(4):
            await asyncio.wait_for(agent._on_mail_delivered(make_event()), timeout=1)

        assert agent._mail_queue.qsize() == 2


class TestSessionTimeout:
    async def test_stuck_session_times_out(self):
        """A session idle beyond session_timeout_seconds is abandoned."""
        config = AgentConfig(id="a-001", name="TestAgent", session_timeout_seconds=0)
        llm = MagicMock()
        llm.chat = AsyncMock(
            return_value=LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        id="tc-1",
                        function_name="sendEmail",
                        arguments={"content": "Done!", "is_complete": True},
                    )
                ],
            )
        )
        agent = _make_agent(config=config, llm_client=llm)
        agent._reflection.enabled = False
        agent.state = AgentState.ACTIVE

        agent._worker_running = True
        agent._mail_queue.put_nowait((_make_message(), {}))

        with patch.object(agent, "_save_interaction_to_memory", new=AsyncMock()):
            task = asyncio.create_task(agent._main_loop())
            try:
                # Give the loop a few passes to reap the timed-out session
                await asyncio.sleep(0.3)
                # Session timed out before ever stepping: no LLM call, no reply
                llm.chat.assert_not_called()
                message_events = [
                    c
                    for c in agent.event_bus.publish.await_args_list
                    if c.args[0].type == EventType.MAIL_NEW
                ]
                assert not message_events
                # Session reaped -> agent falls back to idle
                assert agent.state == AgentState.ACTIVE
            finally:
                agent._worker_running = False
                await asyncio.wait_for(task, timeout=2)
