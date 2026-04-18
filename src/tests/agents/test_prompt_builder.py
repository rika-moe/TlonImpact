"""Tests for SystemPromptBuilder with AgentPromptContext."""

from tlon.agents.prompt_builder import SystemPromptBuilder
from tlon.agents.prompt_context import AgentPromptContext
from tlon.base.config import AgentConfig


def _make_config(agent_id="a-001", name="林知遥", identity="", profile="", behavior=""):
    return AgentConfig(
        id=agent_id, name=name, identity=identity, profile=profile, behavior=behavior
    )


def _make_context(config=None, team_profiles=None):
    config = config or _make_config()
    return AgentPromptContext(
        config=config,
        team_profiles=team_profiles or [],
    )


class TestSystemPromptBuilderIdentity:
    def test_build_identity_contains_name(self):
        ctx = _make_context(config=_make_config(name="江予白"))
        prompt = SystemPromptBuilder(ctx).build_identity()
        assert "江予白" in prompt

    def test_build_identity_includes_identity_text(self):
        ctx = _make_context(config=_make_config(identity="你是一名运维工程师。"))
        prompt = SystemPromptBuilder(ctx).build_identity()
        assert "你是一名运维工程师。" in prompt

    def test_build_identity_without_identity_text(self):
        ctx = _make_context(config=_make_config(name="测试员"))
        prompt = SystemPromptBuilder(ctx).build_identity()
        assert "你是 测试员。" in prompt
        assert "\n\n" not in prompt  # No extra blank line when identity is empty


class TestSystemPromptBuilderTeam:
    def test_build_team_returns_none_when_empty(self):
        ctx = _make_context(team_profiles=[])
        assert SystemPromptBuilder(ctx).build_team() is None

    def test_build_team_includes_teammate_profiles(self):
        ctx = _make_context(
            config=_make_config(name="林知遥"),
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
        ctx = _make_context(config=_make_config(behavior=""))
        assert SystemPromptBuilder(ctx).build_behavior() is None

    def test_build_behavior_includes_text(self):
        ctx = _make_context(config=_make_config(behavior="收到@时响应。"))
        result = SystemPromptBuilder(ctx).build_behavior()
        assert "## 行为守则" in result
        assert "收到@时响应。" in result


class TestSystemPromptBuilderProtocol:
    def test_build_protocol_is_hardcoded(self):
        ctx = _make_context()
        result = SystemPromptBuilder(ctx).build_protocol()
        assert "## 交互协议" in result
        assert "completeDialog" in result
        assert "mentions" in result


class TestSystemPromptBuilderBuild:
    def test_build_returns_non_empty_string(self):
        ctx = _make_context(config=_make_config(name="林知遥"))
        result = SystemPromptBuilder(ctx).build()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_build_is_sync(self):
        """build() must be synchronous — no async needed."""
        import inspect

        ctx = _make_context()
        assert not inspect.iscoroutinefunction(SystemPromptBuilder(ctx).build)

    def test_build_includes_identity_and_protocol(self):
        ctx = _make_context(config=_make_config(name="林知遥"))
        result = SystemPromptBuilder(ctx).build()
        assert "林知遥" in result
        assert "## 交互协议" in result

    def test_build_skips_empty_sections(self):
        ctx = _make_context(
            config=_make_config(name="测试员"),
            team_profiles=[],
        )
        result = SystemPromptBuilder(ctx).build()
        # Should not contain "## 团队成员" or "## 行为守则" when empty
        assert "## 团队成员" not in result
        assert "## 行为守则" not in result

    def test_build_full_prompt(self):
        ctx = _make_context(
            config=_make_config(
                name="林知遥",
                identity="你是一名开发工程师。",
                behavior="收到@时响应。",
            ),
            team_profiles=[("苏晚晴", "产品经理。")],
        )
        result = SystemPromptBuilder(ctx).build()
        assert "你是 林知遥。" in result
        assert "你是一名开发工程师。" in result
        assert "### 苏晚晴" in result
        assert "## 行为守则" in result
        assert "## 交互协议" in result
