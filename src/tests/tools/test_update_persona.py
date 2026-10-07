"""Tests for the persona self-edit tool (tools/agents/update_persona.py)."""

import pytest

from tlon.base.paths import TlonPaths
from tlon.tools.agents.update_persona import UpdatePersonaTool

AGENT_ID = "a-001"


@pytest.fixture
def paths(tmp_path):
    return TlonPaths(home=tmp_path)


@pytest.fixture
def tool(paths):
    return UpdatePersonaTool(tlon_paths=paths, agent_id=AGENT_ID)


class TestUpdatePersonaTool:
    async def test_updates_identity_file(self, tool, paths):
        result = await tool.execute(file="identity", content="# 我是谁\n\n核心自我认知。")

        assert result.success is True
        assert result.data["file"] == "identity"
        assert result.data["bytes"] == len("# 我是谁\n\n核心自我认知。".encode())
        target = paths.agent_identity_file(AGENT_ID)
        assert target.read_text(encoding="utf-8") == "# 我是谁\n\n核心自我认知。"

    async def test_updates_soul_file(self, tool, paths):
        result = await tool.execute(file="soul", content="角色定位与工作风格")

        assert result.success is True
        target = paths.agent_soul_file(AGENT_ID)
        assert target.read_text(encoding="utf-8") == "角色定位与工作风格"

    async def test_full_replacement_overwrites_existing(self, tool, paths):
        target = paths.agent_identity_file(AGENT_ID)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("旧人格", encoding="utf-8")

        result = await tool.execute(file="identity", content="新人格")

        assert result.success is True
        assert target.read_text(encoding="utf-8") == "新人格"

    async def test_invalid_file_value_rejected(self, tool, paths):
        result = await tool.execute(file="rules", content="x")

        assert result.success is False
        assert "RULES.md" in result.error

    async def test_empty_content_rejected(self, tool, paths):
        for content in ("", "   \n\t  "):
            result = await tool.execute(file="identity", content=content)
            assert result.success is False
            assert "content" in result.error
        assert not paths.agent_identity_file(AGENT_ID).exists()

    async def test_other_member_untouched(self, tool, paths):
        await tool.execute(file="identity", content="我自己的人格")

        other = paths.agent_identity_file("a-002")
        assert not other.exists()

    def test_schema_has_no_agent_id_parameter(self, tool):
        """Structural privacy: the target member binds at construction, so the
        LLM can never steer the tool at another member's files."""
        assert "agent_id" not in tool.parameters["properties"]
        assert tool.parameters["required"] == ["file", "content"]

    def test_file_enum_excludes_rules(self, tool):
        """RULES.md is human-edited (ADR 0024) and unreachable via this tool."""
        enum = tool.parameters["properties"]["file"]["enum"]
        assert set(enum) == {"identity", "soul"}
