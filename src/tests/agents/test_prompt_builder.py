"""Tests for SystemPromptBuilder with .md file-based persona."""

import tempfile
from pathlib import Path

from tlon.agents.prompt_builder import SystemPromptBuilder
from tlon.agents.prompt_context import AgentPromptContext


def _make_context(agent_name="林知遥", home=None, identity="", soul="", rules="", team_profiles=None):
    if home is None:
        home = Path(tempfile.mkdtemp())
    config_dir = home / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    if identity:
        (config_dir / "IDENTITY.md").write_text(identity.strip(), encoding="utf-8")
    if soul:
        (config_dir / "SOUL.md").write_text(soul.strip(), encoding="utf-8")
    if rules:
        (config_dir / "RULES.md").write_text(rules.strip(), encoding="utf-8")
    return AgentPromptContext(
        agent_name=agent_name,
        agent_home=str(home),
        team_profiles=team_profiles or [],
    )


class TestSystemPromptBuilderIdentity:
    def test_build_identity_contains_name(self):
        ctx = _make_context(agent_name="江予白")
        prompt = SystemPromptBuilder(ctx).build_identity()
        assert "江予白" in prompt

    def test_build_identity_includes_identity_text(self):
        ctx = _make_context(identity="你是一名运维工程师。")
        prompt = SystemPromptBuilder(ctx).build_identity()
        assert "你是一名运维工程师。" in prompt

    def test_build_identity_without_identity_text(self):
        ctx = _make_context(agent_name="测试员")
        prompt = SystemPromptBuilder(ctx).build_identity()
        assert "你是 测试员。" in prompt

    def test_build_identity_includes_soul_text(self):
        ctx = _make_context(soul="产品经理。负责需求分析。")
        prompt = SystemPromptBuilder(ctx).build_identity()
        assert "产品经理。负责需求分析。" in prompt


class TestSystemPromptBuilderTeam:
    def test_build_team_returns_none_when_empty(self):
        ctx = _make_context(team_profiles=[])
        assert SystemPromptBuilder(ctx).build_team() is None

    def test_build_team_includes_teammate_profiles(self):
        ctx = _make_context(
            agent_name="林知遥",
            team_profiles=[("苏晚晴", "产品经理。负责需求分析。")],
        )
        result = SystemPromptBuilder(ctx).build_team()
        assert "## 团队成员" in result
        assert "### 苏晚晴" in result
        assert "产品经理。负责需求分析。" in result

    def test_build_team_with_multiple_members(self):
        ctx = _make_context(
            team_profiles=[
                ("苏晚晴", "产品经理。"),
                ("江予白", "运维工程师。"),
            ],
        )
        result = SystemPromptBuilder(ctx).build_team()
        assert "### 苏晚晴" in result
        assert "### 江予白" in result


class TestSystemPromptBuilderBehavior:
    def test_build_behavior_returns_none_when_empty(self):
        ctx = _make_context()
        assert SystemPromptBuilder(ctx).build_behavior() is None

    def test_build_behavior_includes_rules_text(self):
        ctx = _make_context(rules="收到@时响应。")
        result = SystemPromptBuilder(ctx).build_behavior()
        assert "## RULES.md" in result
        assert "收到@时响应。" in result


class TestSystemPromptBuilderProtocol:
    def test_build_protocol_is_hardcoded(self):
        ctx = _make_context()
        result = SystemPromptBuilder(ctx).build_protocol()
        assert "## 交互协议" in result
        assert "sendMessage" in result
        assert "is_complete" in result
        assert "mentions" in result


class TestSystemPromptBuilderBuild:
    def test_build_returns_non_empty_string(self):
        ctx = _make_context(agent_name="林知遥")
        result = SystemPromptBuilder(ctx).build()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_build_is_sync(self):
        import inspect

        ctx = _make_context()
        assert not inspect.iscoroutinefunction(SystemPromptBuilder(ctx).build)

    def test_build_includes_identity_and_protocol(self):
        ctx = _make_context(agent_name="林知遥")
        result = SystemPromptBuilder(ctx).build()
        assert "林知遥" in result
        assert "## 交互协议" in result

    def test_build_skips_empty_sections(self):
        ctx = _make_context(agent_name="测试员", team_profiles=[])
        result = SystemPromptBuilder(ctx).build()
        assert "## 团队成员" not in result
        assert "## RULES.md" not in result

    def test_build_full_prompt(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ctx = _make_context(
                agent_name="林知遥",
                home=Path(tmpdir),
                identity="你是一名开发工程师。",
                rules="收到@时响应。",
                team_profiles=[("苏晚晴", "产品经理。")],
            )
            result = SystemPromptBuilder(ctx).build()
            assert "你是 林知遥。" in result
            assert "你是一名开发工程师。" in result
            assert "### 苏晚晴" in result
            assert "## RULES.md" in result
            assert "## 交互协议" in result
