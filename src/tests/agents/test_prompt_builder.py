"""Tests for SystemPromptBuilder with AgentPromptContext."""

from unittest.mock import MagicMock

from tlon.agents.prompt_builder import SystemPromptBuilder
from tlon.agents.prompt_context import AgentPromptContext
from tlon.base.config.models import AgentConfig, RoleConfig


def _make_config(agent_id="a-001", name="林知遥", role=None):
    return AgentConfig(id=agent_id, name=name, personality="冷静", role=role)


def _make_tool_def(name, description):
    return {"function": {"name": name, "description": description}}


def _make_context(config=None, tool_definitions=None, memory_blocks=None, team_members=None):
    prompt_manager = MagicMock()
    prompt_manager.build_workflow_prompt.return_value = "workflow text"
    prompt_manager.build_important_notes_prompt.return_value = "notes text"

    return AgentPromptContext(
        config=config or _make_config(),
        team_members=team_members or [],
        tool_definitions=tool_definitions or [],
        memory_blocks=memory_blocks or [],
        prompt_manager=prompt_manager,
    )


class TestSystemPromptBuilderIdentity:
    def test_build_identity_contains_name(self):
        ctx = _make_context(config=_make_config(name="江予白"))
        prompt = SystemPromptBuilder(ctx).build_identity()
        assert "江予白" in prompt

    def test_build_identity_contains_role_title(self):
        role = RoleConfig(title="开发工程师", jd="负责后端开发")
        ctx = _make_context(config=_make_config(role=role))
        prompt = SystemPromptBuilder(ctx).build_identity()
        assert "开发工程师" in prompt

    def test_build_identity_falls_back_to_name_when_no_role(self):
        ctx = _make_context(config=_make_config(name="测试员", role=None))
        prompt = SystemPromptBuilder(ctx).build_identity()
        assert "测试员" in prompt


class TestSystemPromptBuilderCapabilities:
    def test_capabilities_reads_from_tool_definitions(self):
        tools = [
            _make_tool_def("executeBash", "在 Docker 中执行 bash 命令"),
            _make_tool_def("completeDialog", "完成对话"),
        ]
        ctx = _make_context(tool_definitions=tools)
        result = SystemPromptBuilder(ctx).build_capabilities()
        assert "executeBash" in result
        assert "在 Docker 中执行 bash 命令" in result
        assert "completeDialog" in result

    def test_capabilities_empty_when_no_tools(self):
        ctx = _make_context(tool_definitions=[])
        result = SystemPromptBuilder(ctx).build_capabilities()
        # Section header present but no tool lines
        assert "## 可用工具" in result

    def test_tool_usage_reads_from_tool_definitions(self):
        tools = [_make_tool_def("rememberFact", "记住一个事实")]
        ctx = _make_context(tool_definitions=tools)
        result = SystemPromptBuilder(ctx).build_tool_usage()
        assert "rememberFact" in result
        assert "记住一个事实" in result

    def test_capabilities_not_hardcoded(self):
        """Descriptions come from tool_definitions, not hardcoded strings."""
        tools = [_make_tool_def("myCustomTool", "我的自定义工具描述")]
        ctx = _make_context(tool_definitions=tools)
        result = SystemPromptBuilder(ctx).build_capabilities()
        assert "myCustomTool" in result
        assert "我的自定义工具描述" in result


class TestSystemPromptBuilderMemory:
    def test_build_memory_returns_none_when_empty(self):
        ctx = _make_context(memory_blocks=[])
        assert SystemPromptBuilder(ctx).build_memory() is None

    def test_build_memory_uses_prefetched_blocks(self):
        block = MagicMock()
        block.to_openai_message.return_value = {"content": "历史记忆内容"}
        ctx = _make_context(memory_blocks=[block])
        result = SystemPromptBuilder(ctx).build_memory()
        assert result is not None
        assert "历史记忆内容" in result

    def test_build_memory_is_sync(self):
        """build_memory must be synchronous (no async/await)."""
        import inspect

        ctx = _make_context()
        assert not inspect.iscoroutinefunction(SystemPromptBuilder(ctx).build_memory)


class TestSystemPromptBuilderBuild:
    def test_build_returns_non_empty_string(self):
        tools = [_make_tool_def("completeDialog", "完成对话")]
        ctx = _make_context(tool_definitions=tools)
        result = SystemPromptBuilder(ctx).build()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_build_is_sync(self):
        """build() must be synchronous — no async needed."""
        import inspect

        ctx = _make_context()
        assert not inspect.iscoroutinefunction(SystemPromptBuilder(ctx).build)

    def test_build_includes_identity_and_tools(self):
        tools = [_make_tool_def("executeBash", "执行命令")]
        ctx = _make_context(
            config=_make_config(name="林知遥"),
            tool_definitions=tools,
        )
        result = SystemPromptBuilder(ctx).build()
        assert "林知遥" in result
        assert "executeBash" in result
