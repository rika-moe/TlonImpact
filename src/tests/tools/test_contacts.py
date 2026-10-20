"""Tests for the contact book tools (tools/agents/contacts.py, ADR 0024)."""

import pytest

from tlon.base.paths import TlonPaths
from tlon.operations.identity import make_identity_operations

AGENT_ID = "a-001"


@pytest.fixture
def paths(tmp_path):
    return TlonPaths(home=tmp_path)


@pytest.fixture
def reader(paths):
    return _tool(paths, "contacts-read")


@pytest.fixture
def writer(paths):
    return _tool(paths, "contacts-upsert")


def _book(paths) -> str:
    return paths.agent_contacts_file(AGENT_ID).read_text(encoding="utf-8")


class TestReadContacts:
    async def test_missing_file_reads_as_empty_book(self, reader, paths):
        result = await reader.execute_context({})

        assert result.success is True
        assert result.data["entries"] == 0
        assert result.data["content"] == ""
        assert "空" in result.message
        # Read side never creates the file.
        assert not paths.agent_contacts_file(AGENT_ID).exists()

    async def test_reads_full_text_with_entry_count(self, reader, writer):
        await writer.execute_context(
            {"name": "林知遥", "address": "lin@tlon.internal", "description": "聪明"}
        )
        await writer.execute_context(
            {"name": "沈默", "address": "shen@tlon.internal", "description": "话少靠谱"}
        )

        result = await reader.execute_context({})

        assert result.success is True
        assert result.data["entries"] == 2
        assert "林知遥" in result.data["content"]
        assert "沈默" in result.data["content"]


class TestUpdateContact:
    async def test_add_creates_file_with_header_and_entry(self, writer, paths):
        result = await writer.execute_context(
            {"name": "林知遥", "address": "lin@tlon.internal", "description": "聪明，做事快"}
        )

        assert result.success is True
        assert result.data["action"] == "added"
        assert _book(paths) == (
            "# 联系簿\n\n## 林知遥\n- 地址：lin@tlon.internal\n- 描述：聪明，做事快\n"
        )

    async def test_same_name_upserts_address_and_description(self, writer, paths):
        await writer.execute_context(
            {"name": "林知遥", "address": "lin@tlon.internal", "description": "聪明"}
        )
        result = await writer.execute_context(
            {"name": "林知遥", "address": "lin2@tlon.internal", "description": "聪明但急躁"}
        )

        assert result.success is True
        assert result.data["action"] == "updated"
        content = _book(paths)
        assert "- 地址：lin2@tlon.internal" in content
        assert "- 描述：聪明但急躁" in content
        assert content.count("## 林知遥") == 1

    async def test_free_text_lines_inside_section_survive_upsert(self, writer, paths):
        await writer.execute_context(
            {"name": "林知遥", "address": "lin@tlon.internal", "description": "聪明"}
        )
        book_file = paths.agent_contacts_file(AGENT_ID)
        book_file.write_text(
            "# 联系簿\n\n## 林知遥\n- 地址：lin@tlon.internal\n- 描述：聪明\n上次合作写规格很顺\n",
            encoding="utf-8",
        )

        await writer.execute_context(
            {"name": "林知遥", "address": "lin@tlon.internal", "description": "很靠谱"}
        )

        content = _book(paths)
        assert "上次合作写规格很顺" in content
        assert "- 描述：很靠谱" in content

    async def test_preamble_survives_writes(self, writer, paths):
        book_file = paths.agent_contacts_file(AGENT_ID)
        book_file.parent.mkdir(parents=True, exist_ok=True)
        book_file.write_text(
            "# 联系簿\n\n我的社交小记：先记熟的。\n\n## 林知遥\n- 地址：lin@tlon.internal\n- 描述：聪明\n",
            encoding="utf-8",
        )

        await writer.execute_context(
            {"name": "沈默", "address": "shen@tlon.internal", "description": "靠谱"}
        )

        content = _book(paths)
        assert "我的社交小记：先记熟的。" in content
        assert "## 沈默" in content

    async def test_empty_description_deletes_entry(self, writer, paths):
        await writer.execute_context(
            {"name": "林知遥", "address": "lin@tlon.internal", "description": "聪明"}
        )
        await writer.execute_context(
            {"name": "沈默", "address": "shen@tlon.internal", "description": "靠谱"}
        )

        result = await writer.execute_context({"name": "林知遥", "description": ""})

        assert result.success is True
        assert result.data["action"] == "deleted"
        content = _book(paths)
        assert "林知遥" not in content
        assert "沈默" in content

    async def test_delete_missing_entry_is_friendly_error(self, writer, paths):
        result = await writer.execute_context({"name": "不存在", "description": ""})

        assert result.success is False
        assert "不存在" in result.error
        assert not paths.agent_contacts_file(AGENT_ID).exists()

    async def test_add_without_address_rejected(self, writer, paths):
        result = await writer.execute_context({"name": "林知遥", "description": "聪明"})

        assert result.success is False
        assert "address" in result.error
        assert not paths.agent_contacts_file(AGENT_ID).exists()

    async def test_empty_name_rejected(self, writer):
        result = await writer.execute_context(
            {"name": "  ", "address": "x@tlon.internal", "description": "y"}
        )

        assert result.success is False

    async def test_other_entries_untouched_by_upsert(self, writer, paths):
        await writer.execute_context(
            {"name": "林知遥", "address": "lin@tlon.internal", "description": "聪明"}
        )
        await writer.execute_context(
            {"name": "沈默", "address": "shen@tlon.internal", "description": "靠谱"}
        )

        await writer.execute_context(
            {"name": "林知遥", "address": "lin@tlon.internal", "description": "更新过"}
        )

        content = _book(paths)
        assert "- 描述：靠谱" in content

    async def test_other_member_untouched(self, writer, paths):
        await writer.execute_context(
            {"name": "林知遥", "address": "lin@tlon.internal", "description": "聪明"}
        )

        assert not paths.agent_contacts_file("a-002").exists()

    def test_schema_has_no_agent_id_parameter(self, reader, writer):
        """Structural privacy: the owning member binds at construction."""
        for tool in (reader, writer):
            assert "agent_id" not in tool.parameters["properties"]

    def test_update_schema_requires_name_and_description(self, writer):
        assert writer.parameters["required"] == ["name", "description"]


def _tool(paths, name):
    from tlon.operations.adapters import OperationTool
    from tlon.operations.core import CallContext, OperationExecutor, OperationRegistry

    return OperationTool(
        OperationExecutor(
            OperationRegistry(make_identity_operations(paths, AGENT_ID)),
            CallContext(AGENT_ID, "run", "call"),
        ),
        name,
    )
