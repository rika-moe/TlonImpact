"""Tests for Agent constructor injection.

Verifies that Agent:
- Accepts all infrastructure deps via constructor (no internal construction)
- Inherits Component
- Exposes correct id/name after construction
- Holds the injected tool_executor reference
"""

from unittest.mock import MagicMock

from tlon.agents.agent import Agent
from tlon.base.component import Component
from tlon.base.config import AgentConfig


def _make_agent(**kwargs):
    """Build an Agent with all deps as MagicMocks."""
    config = AgentConfig(id="a-001", name="林知遥", identity="技术能力强")

    defaults = {
        "config": config,
        "event_bus": MagicMock(),
        "llm_client": MagicMock(),
        "docker_runtime": MagicMock(),
        "memory": MagicMock(),
        "task_manager": MagicMock(),
        "tool_executor": MagicMock(),
    }
    defaults.update(kwargs)
    return Agent(**defaults)


class TestAgentInheritance:
    def test_agent_is_component(self):
        agent = _make_agent()
        assert isinstance(agent, Component)

    def test_agent_has_initialize_method(self):
        agent = _make_agent()
        assert hasattr(agent, "initialize")

    def test_agent_has_shutdown_method(self):
        agent = _make_agent()
        assert hasattr(agent, "shutdown")


class TestAgentIdentity:
    def test_id_set_from_config(self):
        config = AgentConfig(id="dev-007", name="测试员")
        agent = _make_agent(config=config)
        assert agent.id == "dev-007"

    def test_name_set_from_config(self):
        config = AgentConfig(id="dev-007", name="测试员")
        agent = _make_agent(config=config)
        assert agent.name == "测试员"

    def test_identity_set_from_config(self):
        config = AgentConfig(id="a-001", name="林知遥", identity="冷静理性")
        agent = _make_agent(config=config)
        assert agent.config.identity == "冷静理性"


class TestAgentInjectedDeps:
    def test_holds_injected_llm_client(self):
        llm = MagicMock()
        agent = _make_agent(llm_client=llm)
        assert agent.llm_client is llm

    def test_holds_injected_docker_runtime(self):
        docker = MagicMock()
        agent = _make_agent(docker_runtime=docker)
        assert agent.docker_runtime is docker

    def test_holds_injected_memory(self):
        memory = MagicMock()
        agent = _make_agent(memory=memory)
        assert agent.memory is memory

    def test_holds_injected_task_manager(self):
        tm = MagicMock()
        agent = _make_agent(task_manager=tm)
        assert agent.task_manager is tm

    def test_holds_injected_tool_executor(self):
        executor = MagicMock()
        agent = _make_agent(tool_executor=executor)
        assert agent.tool_executor is executor

    def test_holds_injected_event_bus(self):
        bus = MagicMock()
        agent = _make_agent(event_bus=bus)
        assert agent.event_bus is bus

    def test_team_members_defaults_to_empty_list(self):
        agent = _make_agent()
        assert agent.team_members == []

    def test_team_members_stored(self):
        other = AgentConfig(id="b-001", name="江予白")
        agent = _make_agent(team_members=[other])
        assert len(agent.team_members) == 1
        assert agent.team_members[0].id == "b-001"


class TestAgentPromptCache:
    def test_prompt_cache_initially_none(self):
        agent = _make_agent()
        assert agent._system_prompt_cache is None

    def test_no_internal_construction(self):
        """Agent should not construct LLMClient or DockerRuntime internally."""
        # If Agent constructed these internally, passing a sentinel would be ignored.
        # Verifying the injected sentinel is what's stored.
        sentinel_llm = object()
        sentinel_docker = object()
        agent = _make_agent(llm_client=sentinel_llm, docker_runtime=sentinel_docker)
        assert agent.llm_client is sentinel_llm
        assert agent.docker_runtime is sentinel_docker
