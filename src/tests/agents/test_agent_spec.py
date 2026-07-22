"""Tests for AgentSpec (tlon.agents.spec) and behavior.tlon loading."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tlon.agents.agent import Agent
from tlon.agents.edn import loads
from tlon.agents.spec import AgentSpec, SpecValidationError
from tlon.agents.tool_executor import ToolExecutor
from tlon.base.config import AgentConfig
from tlon.base.events.event import EventType
from tlon.base.llm_client import LLMResponse, ToolCall
from tlon.base.paths import TlonPaths
from tlon.organization.messaging.message import Message
from tlon.tools.base import ToolResult

FULL_DEFAULT_EDN = """
{:version 1
 :trigger {:on-mention true :proactive false}
 :context {:persona true :protocol true :knowledge true :room-history 20}
 :loop {:mode :react :max-iterations 10 :tools :background :max-consecutive-errors 3}
 :on-complete {:save-interaction true :reflect {:enabled true :interval-minutes 15}}}
"""


def _fake_agent(**config_kwargs) -> SimpleNamespace:
    """A duck-typed agent carrying real config + memory config objects."""
    config = AgentConfig(id="a-001", name="TestAgent", **config_kwargs)
    memory_config = SimpleNamespace(reflection_enabled=True, reflection_interval_minutes=15)
    return SimpleNamespace(config=config, memory=SimpleNamespace(config=memory_config))


class TestAgentSpecSemantics:
    def test_from_edn_full_defaults_matches_default_spec(self):
        spec = AgentSpec.from_edn(loads(FULL_DEFAULT_EDN))
        assert spec == AgentSpec.default(_fake_agent())

    def test_to_dict_from_dict_roundtrip(self):
        spec = AgentSpec.default(_fake_agent(proactive=True, max_iterations=7))
        assert AgentSpec.from_dict(spec.to_dict()) == spec

    def test_version_required(self):
        with pytest.raises(SpecValidationError, match=":version"):
            AgentSpec.from_edn(loads("{:trigger {:proactive true}}"))

    def test_version_unsupported(self):
        with pytest.raises(SpecValidationError, match="version"):
            AgentSpec.from_edn(loads("{:version 2}"))

    def test_type_error_reports_field(self):
        with pytest.raises(SpecValidationError, match="max-iterations"):
            AgentSpec.from_edn(loads('{:version 1 :loop {:max-iterations "ten"}}'))

    def test_bool_field_rejects_int(self):
        with pytest.raises(SpecValidationError, match=":proactive"):
            AgentSpec.from_edn(loads("{:version 1 :trigger {:proactive 1}}"))

    def test_unknown_keys_ignored(self):
        spec = AgentSpec.from_edn(
            loads('{:version 1 :future-feature [1 2] :trigger {:proactive true :bogus 1}}')
        )
        assert spec.trigger.proactive is True

    def test_top_level_must_be_map(self):
        with pytest.raises(SpecValidationError, match="top-level map"):
            AgentSpec.from_edn(loads("[1 2 3]"))

    def test_loop_mode_enum(self):
        spec = AgentSpec.from_edn(loads("{:version 1 :loop {:mode :react}}"))
        assert spec.loop.mode == "react"
        with pytest.raises(SpecValidationError, match=":mode"):
            AgentSpec.from_edn(loads("{:version 1 :loop {:mode :plan-execute}}"))

    def test_partial_spec_inherits_base(self):
        base = AgentSpec.default(_fake_agent(proactive=True))
        spec = AgentSpec.from_edn(loads("{:version 1 :loop {:max-iterations 1}}"), base=base)
        assert spec.loop.max_iterations == 1
        assert spec.trigger.proactive is True  # inherited from base, not reset


# ── behavior.tlon file loading (Agent integration) ──


def _make_agent(**kwargs) -> Agent:
    """Build an Agent with all deps as mocks."""
    config = kwargs.pop("config", None) or AgentConfig(id="a-001", name="TestAgent")

    event_bus = MagicMock()
    event_bus.publish = AsyncMock()
    event_bus.subscribe = MagicMock(return_value="sub-id")
    event_bus.unsubscribe = MagicMock()

    defaults = {
        "config": config,
        "event_bus": event_bus,
        "llm_client": MagicMock(),
        "memory": AsyncMock(),
        "task_manager": MagicMock(),
        "tool_executor": ToolExecutor(),
    }
    defaults.update(kwargs)
    return Agent(**defaults)


def _use_tmp_paths(monkeypatch, tmp_path) -> TlonPaths:
    """Redirect the paths singleton used by spec loading to a tmp dir."""
    test_paths = TlonPaths(home=tmp_path)
    monkeypatch.setattr("tlon.agents.spec.paths", test_paths)
    return test_paths


def _write_behavior(test_paths: TlonPaths, agent_id: str, content: str) -> None:
    behavior_file = test_paths.agent_config_dir(agent_id) / "behavior.tlon"
    behavior_file.write_text(content, encoding="utf-8")


class TestBehaviorFileLoading:
    def test_missing_file_uses_default_spec(self, tmp_path, monkeypatch):
        _use_tmp_paths(monkeypatch, tmp_path)
        agent = _make_agent(config=AgentConfig(id="a-001", name="TestAgent", max_iterations=7))
        assert agent.spec.loop.max_iterations == 7

    def test_behavior_file_overrides_defaults(self, tmp_path, monkeypatch):
        test_paths = _use_tmp_paths(monkeypatch, tmp_path)
        _write_behavior(test_paths, "a-001", "{:version 1 :loop {:max-iterations 1}}")
        agent = _make_agent(
            config=AgentConfig(id="a-001", name="TestAgent", proactive=True, max_iterations=7)
        )
        assert agent.spec.loop.max_iterations == 1
        # Unspecified fields inherit DefaultSpec (config-derived), not static defaults
        assert agent.spec.trigger.proactive is True

    def test_malformed_file_falls_back_to_default(self, tmp_path, monkeypatch):
        test_paths = _use_tmp_paths(monkeypatch, tmp_path)
        _write_behavior(test_paths, "a-001", "{:version 1 :loop {:max-iterations")
        with patch("tlon.agents.spec.logger") as mock_logger:
            agent = _make_agent(config=AgentConfig(id="a-001", name="TestAgent"))
        assert agent.spec.loop.max_iterations == 10
        warning_events = [c.args[0] for c in mock_logger.warning.call_args_list]
        assert "agent_spec_load_failed" in warning_events

    def test_invalid_spec_falls_back_to_default(self, tmp_path, monkeypatch):
        test_paths = _use_tmp_paths(monkeypatch, tmp_path)
        _write_behavior(test_paths, "a-001", "{:version 1 :loop {:max-iterations \"ten\"}}")
        with patch("tlon.agents.spec.logger") as mock_logger:
            agent = _make_agent(config=AgentConfig(id="a-001", name="TestAgent"))
        assert agent.spec.loop.max_iterations == 10
        warning_events = [c.args[0] for c in mock_logger.warning.call_args_list]
        assert "agent_spec_load_failed" in warning_events


class TestSpecDrivenBehavior:
    async def test_max_iterations_one_gives_up_early(self, tmp_path, monkeypatch):
        """behavior.tlon {:loop {:max-iterations 1}} shortens the ReAct loop."""
        test_paths = _use_tmp_paths(monkeypatch, tmp_path)
        _write_behavior(test_paths, "a-001", "{:version 1 :loop {:max-iterations 1}}")

        llm = MagicMock()
        llm.chat = AsyncMock(
            return_value=LLMResponse(
                content=None,
                tool_calls=[ToolCall(id="tc-1", function_name="testTool", arguments={})],
            )
        )
        agent = _make_agent(llm_client=llm)

        mock_tool = MagicMock()
        mock_tool.name = "testTool"
        mock_tool.execute = AsyncMock(return_value=ToolResult(success=True, data={}))
        agent.tool_executor.register_tool(mock_tool)

        msg = Message.create(
            sender_id="user-001", sender_name="User", sender_type="human", content="hi"
        )
        await agent._process_message(msg)

        # Gave up after 1 iteration with the max-iterations message
        message_events = [
            c
            for c in agent.event_bus.publish.await_args_list
            if c.args[0].type == EventType.MESSAGE_NEW
        ]
        assert len(message_events) == 1
        assert "简化" in message_events[0].args[0].payload.content
        # LLM was called exactly once (second step hit the iteration limit)
        assert llm.chat.await_count == 1

    async def test_trigger_proactive_overrides_config(self, tmp_path, monkeypatch):
        """behavior.tlon {:trigger {:proactive false}} wins over config.proactive."""
        test_paths = _use_tmp_paths(monkeypatch, tmp_path)
        _write_behavior(test_paths, "a-001", "{:version 1 :trigger {:proactive false}}")
        agent = _make_agent(config=AgentConfig(id="a-001", name="TestAgent", proactive=True))

        human_msg = Message.create(
            sender_id="user-001", sender_name="User", sender_type="human", content="hello"
        )
        assert agent._should_respond(human_msg) is False

        mention_msg = Message.create(
            sender_id="user-001",
            sender_name="User",
            sender_type="human",
            content="hi @TestAgent",
            mentions=["TestAgent"],
        )
        assert agent._should_respond(mention_msg) is True

    async def test_room_history_limit_from_spec(self, tmp_path, monkeypatch):
        test_paths = _use_tmp_paths(monkeypatch, tmp_path)
        _write_behavior(test_paths, "a-001", "{:version 1 :context {:room-history 5}}")

        message_store = AsyncMock()
        message_store.get_history = AsyncMock(return_value=[])
        agent = _make_agent(message_store=message_store)

        msg = Message.create(
            sender_id="user-001", sender_name="User", sender_type="human", content="hi"
        )
        await agent._build_context(msg)
        message_store.get_history.assert_awaited_once_with(limit=5, before_id=msg.id)

    def test_reflect_interval_from_spec(self, tmp_path, monkeypatch):
        test_paths = _use_tmp_paths(monkeypatch, tmp_path)
        _write_behavior(
            test_paths,
            "a-001",
            "{:version 1 :on-complete {:reflect {:enabled false :interval-minutes 7}}}",
        )
        agent = _make_agent()
        assert agent._reflection.enabled is False
        assert agent._reflection._interval == 7 * 60
