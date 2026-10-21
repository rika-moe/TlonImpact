"""Tests for the persona self-edit tool (tools/agents/update_persona.py)."""

import hashlib
import json

import pytest

from tlon.base.ledger import LedgerWriter, collect_agent_ledger, read_ledger
from tlon.base.paths import TlonPaths
from tlon.operations.identity import make_identity_operations

AGENT_ID = "a-001"


@pytest.fixture
def paths(tmp_path):
    return TlonPaths(home=tmp_path)


@pytest.fixture
def tool(paths):
    return _tool(paths, "persona-update")


class TestPersonaService:
    async def test_updates_identity_file(self, tool, paths):
        result = await tool.execute_context(
            {"file": "identity", "content": "# 我是谁\n\n核心自我认知。"}
        )

        assert result.success is True
        assert result.data["file"] == "identity"
        assert result.data["bytes"] == len("# 我是谁\n\n核心自我认知。".encode())
        target = paths.agent_identity_file(AGENT_ID)
        assert target.read_text(encoding="utf-8") == "# 我是谁\n\n核心自我认知。"

    async def test_updates_soul_file(self, tool, paths):
        result = await tool.execute_context({"file": "soul", "content": "角色定位与工作风格"})

        assert result.success is True
        target = paths.agent_soul_file(AGENT_ID)
        assert target.read_text(encoding="utf-8") == "角色定位与工作风格"

    async def test_full_replacement_overwrites_existing(self, tool, paths):
        target = paths.agent_identity_file(AGENT_ID)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("旧人格", encoding="utf-8")

        result = await tool.execute_context({"file": "identity", "content": "新人格"})

        assert result.success is True
        assert target.read_text(encoding="utf-8") == "新人格"

    async def test_invalid_file_value_rejected(self, tool, paths):
        result = await tool.execute_context({"file": "rules", "content": "x"})

        assert result.success is False
        assert "rules" in result.error

    async def test_empty_content_rejected(self, tool, paths):
        for content in ("", "   \n\t  "):
            result = await tool.execute_context({"file": "identity", "content": content})
            assert result.success is False
            assert "content" in result.error
        assert not paths.agent_identity_file(AGENT_ID).exists()

    async def test_other_member_untouched(self, tool, paths):
        await tool.execute_context({"file": "identity", "content": "我自己的人格"})

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


class TestPersonaLedgerAudit:
    """ADR 0024 决策 2: every successful rewrite appends a persona_update
    record to the member's ledger.jsonl — accountability, not approval."""

    async def test_success_appends_persona_update_record(self, tool, paths):
        content = "# 我是谁\n\n核心自我认知。"
        result = await tool.execute_context({"file": "identity", "content": content})

        assert result.success is True
        assert "留痕失败" not in result.message
        records = await read_ledger(paths.agent_ledger_file(AGENT_ID))
        assert len(records) == 1
        record = records[0]
        assert record["event"] == "persona_update"
        assert record["agent_id"] == AGENT_ID
        assert record["file"] == "identity"
        assert record["sha256"] == hashlib.sha256(content.encode("utf-8")).hexdigest()
        assert record["bytes"] == len(content.encode("utf-8"))
        assert record["ts"]

    async def test_each_rewrite_appends_one_record(self, tool, paths):
        await tool.execute_context({"file": "identity", "content": "第一版"})
        await tool.execute_context({"file": "soul", "content": "灵魂第一版"})

        records = await read_ledger(paths.agent_ledger_file(AGENT_ID))
        assert [r["file"] for r in records] == ["identity", "soul"]

    async def test_failed_call_writes_no_ledger_record(self, tool, paths):
        await tool.execute_context({"file": "rules", "content": "x"})
        await tool.execute_context({"file": "identity", "content": "   "})

        assert not paths.agent_ledger_file(AGENT_ID).exists()

    async def test_ledger_failure_stays_success_but_is_flagged(
        self, tool, paths, monkeypatch: pytest.MonkeyPatch
    ):
        """Audit failure never passes silently: the file write already
        succeeded, so the result stays a success, but the message flags
        the missing trail."""

        async def _boom(record: dict) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(LedgerWriter, "append", _boom)

        result = await tool.execute_context({"file": "identity", "content": "新人格"})

        assert result.success is True
        assert "留痕失败" in result.message
        assert paths.agent_identity_file(AGENT_ID).read_text(encoding="utf-8") == "新人格"

    async def test_persona_records_do_not_pollute_metabolic_summary(self, tool, paths):
        """The same ledger.jsonl hosts llm_call consumption records; the
        metabolic fold must ignore persona_update (calls is the efficiency
        denominator)."""
        llm_record = {
            "ts": "2026-10-07T00:00:00+00:00",
            "event": "llm_call",
            "model": "openai/gpt-test",
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "duration_ms": 100,
            "cpu_ms": 1,
            "purpose": "think",
            "session_id": "s1",
        }
        await LedgerWriter(paths.agent_ledger_file(AGENT_ID)).append(llm_record)
        await tool.execute_context({"file": "identity", "content": "人格"})

        summary = await collect_agent_ledger(AGENT_ID, tlon_paths=paths)
        assert summary["calls"] == 1
        assert summary["prompt_tokens"] == 10
        assert summary["completion_tokens"] == 5
        assert summary["total_tokens"] == 15
        assert summary["duration_ms"] == 100
        assert list(summary["by_purpose"]) == ["think"]
        assert list(summary["by_model"]) == ["openai/gpt-test"]

        # The raw records stay readable for audit purposes.
        raw = [
            json.loads(line)
            for line in paths.agent_ledger_file(AGENT_ID).read_text(encoding="utf-8").splitlines()
        ]
        assert [r["event"] for r in raw] == ["llm_call", "persona_update"]


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
