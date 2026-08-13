"""Tests for AgentStore."""

import tempfile
from pathlib import Path

from tlon.app.wizard.agent_store import AgentStore
from tlon.base.config import AgentConfig, DockerRuntimeConfig, LLMConfig
from tlon.base.paths import TlonPaths


def _make_store(tmpdir: str) -> AgentStore:
    return AgentStore(paths=TlonPaths(home=Path(tmpdir)))


class TestAgentStore:
    """Test cases for AgentStore."""

    def test_save_and_load_agent(self):
        """Test saving and loading an agent."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = _make_store(tmpdir)

            agent = AgentConfig(
                id="test-001",
                name="测试 Agent",
                llm=LLMConfig(model_name="gpt-4", temperature=0.5),
                docker=DockerRuntimeConfig(image="ubuntu:22.04", memory_limit="1g"),
            )

            success, msg = store.save_agent(agent)
            assert success

            loaded = store.get_agent("test-001")
            assert loaded is not None
            assert loaded.name == "测试 Agent"
            assert loaded.llm is not None

    def test_get_agent_by_name(self):
        """Test getting agent by name."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = _make_store(tmpdir)

            agent = AgentConfig(
                id="test-002",
                name="Agent By Name",
            )
            store.save_agent(agent)

            loaded = store.get_agent_by_name("Agent By Name")
            assert loaded is not None
            assert loaded.id == "test-002"

    def test_agent_exists(self):
        """Test checking if agent exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = _make_store(tmpdir)

            agent = AgentConfig(
                id="existing",
                name="Existing Agent",
            )
            store.save_agent(agent)

            assert store.agent_exists("existing")
            assert not store.agent_exists("nonexistent")

    def test_delete_agent(self):
        """Deleting an agent removes its entire home directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = _make_store(tmpdir)

            agent = AgentConfig(
                id="deletable",
                name="Deletable Agent",
            )
            store.save_agent(agent)
            assert store.agent_exists("deletable")

            success, msg = store.delete_agent("deletable")
            assert success
            assert not (Path(tmpdir) / "deletable").exists()
            # NOTE: agent_exists() recreates the config dir as a side effect
            # (agent_config_file mkdirs), so it must run after the rmtree check.
            assert not store.agent_exists("deletable")

    def test_generate_agent_id(self):
        """Test generating unique agent IDs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = _make_store(tmpdir)

            existing = ["developer-001", "developer-002"]
            new_id = store.generate_agent_id("developer", existing)

            assert new_id == "developer-003"
            assert new_id not in existing

    def test_validate_agent_data(self):
        """Test agent data validation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = _make_store(tmpdir)

            # Valid data
            valid, errors = store.validate_agent_data(
                {
                    "id": "valid-id",
                    "name": "Valid Name",
                }
            )
            assert valid
            assert len(errors) == 0

            # Missing required fields
            valid, errors = store.validate_agent_data(
                {
                    "id": "",
                    "name": "",
                }
            )
            assert not valid
            assert len(errors) > 0

    def test_write_behavior_file(self):
        """Written behavior.tlon parses as a complete, valid unified spec."""
        from tlon.agents.edn import loads
        from tlon.agents.member_spec import MemberSpec

        with tempfile.TemporaryDirectory() as tmpdir:
            store = _make_store(tmpdir)
            store.save_agent(AgentConfig(id="behaved", name="Behaved Agent"))

            store.write_behavior_file("behaved")

            behavior_file = TlonPaths(home=Path(tmpdir)).agent_behavior_file("behaved")
            assert behavior_file.exists()
            spec = MemberSpec.from_edn(loads(behavior_file.read_text(encoding="utf-8")))
            assert spec.version == 1
            # The canonical ReAct program: the cognitive respond rule plus
            # the phase-E boot rule (declares startup via :on :boot).
            assert len(spec.rules) == 2
            assert spec.rules[0].loop is not None
            assert spec.rules[1].name == "boot"
            assert spec.rules[1].on is None  # :on :boot

    def test_write_behavior_file_resident_personalized(self):
        """A known resident gets its personalized behavior, not the canonical."""
        from tlon.agents.behavior_templates import standard_behavior_edn
        from tlon.agents.edn import loads
        from tlon.agents.member_spec import MemberSpec

        with tempfile.TemporaryDirectory() as tmpdir:
            store = _make_store(tmpdir)
            store.save_agent(AgentConfig(id="lin-zhiyao", name="林知遥"))

            store.write_behavior_file("lin-zhiyao")

            behavior_file = TlonPaths(home=Path(tmpdir)).agent_behavior_file("lin-zhiyao")
            text = behavior_file.read_text(encoding="utf-8")
            assert text != standard_behavior_edn()  # personalized, not canonical
            spec = MemberSpec.from_edn(loads(text))
            # 开发工程师的深度迭代：max-iterations 20
            assert spec.rules[0].loop is not None
            assert spec.rules[0].loop.max_iterations == 20

    def test_write_behavior_file_gu_yanxi_mixed(self):
        """客服 gu-yanxi 是阶段 F 混排狗粮：auto-optout 确定性规则前置 +
        respond 认知规则（deflection）——共 3 条规则。"""
        from tlon.agents.edn import loads
        from tlon.agents.member_spec import MemberSpec

        with tempfile.TemporaryDirectory() as tmpdir:
            store = _make_store(tmpdir)
            store.save_agent(AgentConfig(id="gu-yanxi", name="顾言蹊"))

            store.write_behavior_file("gu-yanxi")

            behavior_file = TlonPaths(home=Path(tmpdir)).agent_behavior_file("gu-yanxi")
            spec = MemberSpec.from_edn(loads(behavior_file.read_text(encoding="utf-8")))
            names = [r.name for r in spec.rules]
            assert names == ["auto-optout", "respond", "boot"]
            # auto-optout is deterministic; respond is cognitive; respond
            # excludes 退订 so the cheap path handles it first (deflection).
            assert spec.rules[0].loop is None
            assert spec.rules[1].loop is not None
            assert spec.rules[1].name == "respond"

    def test_write_behavior_file_non_resident_canonical(self):
        """A non-resident agent still gets the canonical ReAct behavior."""
        from tlon.agents.behavior_templates import standard_behavior_edn

        with tempfile.TemporaryDirectory() as tmpdir:
            store = _make_store(tmpdir)
            store.save_agent(AgentConfig(id="plain-001", name="Plain Agent"))

            store.write_behavior_file("plain-001")

            behavior_file = TlonPaths(home=Path(tmpdir)).agent_behavior_file("plain-001")
            assert behavior_file.read_text(encoding="utf-8") == standard_behavior_edn()
