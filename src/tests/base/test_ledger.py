"""Tests for the metabolic ledger (issue ).

Write side: ``LLMClient.chat`` with an ``agent_id`` appends one record
per usage-carrying response to ``{agent_id}/ledger.jsonl``. Read side:
``collect_agent_ledger`` / ``collect_lineage_ledgers`` derive summaries
from the raw files, never storing anything.
"""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from tlon.base.config import AgentConfig, LLMConfig, yaml_save
from tlon.base.ledger import (
    LedgerWriter,
    collect_agent_ledger,
    collect_lineage_ledgers,
    read_ledger,
)
from tlon.base.llm_client import LLMClient
from tlon.base.paths import TlonPaths


def _fake_litellm_response(prompt_tokens: int = 3, completion_tokens: int = 5) -> SimpleNamespace:
    message = SimpleNamespace(content="hi", tool_calls=None, reasoning_content=None)
    choice = SimpleNamespace(message=message, finish_reason="stop")
    usage = SimpleNamespace(
        model_dump=lambda: {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        }
    )
    return SimpleNamespace(choices=[choice], usage=usage)


def _client(
    tlon_paths: TlonPaths,
    agent_id: str | None = "dev-001",
    model_name: str = "openai/gpt-test",
) -> LLMClient:
    config = LLMConfig(model_name=model_name, auth_token="sk-test", max_retries=1)
    return LLMClient(config, agent_id=agent_id, tlon_paths=tlon_paths)


def _mock_litellm(monkeypatch: pytest.MonkeyPatch, response: Any) -> None:
    monkeypatch.setattr("litellm.acompletion", AsyncMock(return_value=response))


async def _write_records(path: Path, records: list[dict[str, Any]]) -> None:
    writer = LedgerWriter(path)
    for record in records:
        await writer.append(record)


def _record(
    purpose: str | None = "think",
    prompt_tokens: int = 3,
    completion_tokens: int = 5,
    duration_ms: int = 10,
    cpu_ms: int = 1,
    session_id: str | None = "s1",
) -> dict[str, Any]:
    return {
        "ts": "2026-09-13T00:00:00+00:00",
        "event": "llm_call",
        "model": "openai/gpt-test",
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "duration_ms": duration_ms,
        "cpu_ms": cpu_ms,
        "purpose": purpose,
        "session_id": session_id,
    }


class TestLedgerRecording:
    async def test_chat_appends_record_with_usage(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        tlon_paths = TlonPaths(home=tmp_path)
        client = _client(tlon_paths)
        _mock_litellm(monkeypatch, _fake_litellm_response(prompt_tokens=7, completion_tokens=11))

        response = await client.chat(
            system_prompt="sys",
            messages=[{"role": "user", "content": "hi"}],
            purpose="think",
            session_id="s1",
        )

        assert response.content == "hi"
        ledger_file = tmp_path / "dev-001" / "ledger.jsonl"
        records = [json.loads(line) for line in ledger_file.read_text().splitlines()]
        assert len(records) == 1
        record = records[0]
        assert record["event"] == "llm_call"
        assert record["model"] == "openai/gpt-test"
        assert record["prompt_tokens"] == 7
        assert record["completion_tokens"] == 11
        assert record["purpose"] == "think"
        assert record["session_id"] == "s1"
        assert isinstance(record["duration_ms"], int) and record["duration_ms"] >= 0
        assert isinstance(record["cpu_ms"], int) and record["cpu_ms"] >= 0
        assert record["ts"]

    async def test_no_agent_id_records_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        tlon_paths = TlonPaths(home=tmp_path)
        client = _client(tlon_paths, agent_id=None)
        _mock_litellm(monkeypatch, _fake_litellm_response())

        await client.chat(system_prompt="sys", messages=[{"role": "user", "content": "hi"}])

        assert list(tmp_path.rglob("ledger.jsonl")) == []

    async def test_no_usage_records_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        tlon_paths = TlonPaths(home=tmp_path)
        client = _client(tlon_paths)
        response = _fake_litellm_response()
        response.usage = None
        _mock_litellm(monkeypatch, response)

        await client.chat(system_prompt="sys", messages=[{"role": "user", "content": "hi"}])

        assert not (tmp_path / "dev-001" / "ledger.jsonl").exists()

    async def test_ledger_write_failure_does_not_break_chat(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        tlon_paths = TlonPaths(home=tmp_path)
        client = _client(tlon_paths)
        _mock_litellm(monkeypatch, _fake_litellm_response())

        async def _boom(record: dict[str, Any]) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(LedgerWriter, "append", _boom)

        response = await client.chat(
            system_prompt="sys", messages=[{"role": "user", "content": "hi"}]
        )
        assert response.content == "hi"


class TestReadLedger:
    async def test_torn_tail_line_is_skipped(self, tmp_path: Path) -> None:
        path = tmp_path / "dev-001" / "ledger.jsonl"
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps(_record()) + "\n" + '{"ts": "2026', encoding="utf-8"
        )

        records = await read_ledger(path)

        assert len(records) == 1

    async def test_missing_file_reads_empty(self, tmp_path: Path) -> None:
        assert await read_ledger(tmp_path / "nobody" / "ledger.jsonl") == []


class TestCollectAgentLedger:
    async def test_totals_and_purpose_split(self, tmp_path: Path) -> None:
        tlon_paths = TlonPaths(home=tmp_path)
        await _write_records(
            tlon_paths.agent_ledger_file("dev-001"),
            [
                _record(purpose="think", prompt_tokens=10, completion_tokens=2, duration_ms=100),
                _record(purpose="think", prompt_tokens=20, completion_tokens=4, duration_ms=200),
                _record(purpose="summarize", prompt_tokens=5, completion_tokens=1, duration_ms=50),
            ],
        )

        summary = await collect_agent_ledger("dev-001", tlon_paths=tlon_paths)

        assert summary["agent_id"] == "dev-001"
        assert summary["calls"] == 3
        assert summary["prompt_tokens"] == 35
        assert summary["completion_tokens"] == 7
        assert summary["total_tokens"] == 42
        assert summary["duration_ms"] == 350
        assert summary["by_purpose"]["think"]["calls"] == 2
        assert summary["by_purpose"]["think"]["total_tokens"] == 36
        assert summary["by_purpose"]["summarize"]["prompt_tokens"] == 5

    async def test_purpose_none_folds_into_unknown(self, tmp_path: Path) -> None:
        tlon_paths = TlonPaths(home=tmp_path)
        await _write_records(
            tlon_paths.agent_ledger_file("dev-001"), [_record(purpose=None)]
        )

        summary = await collect_agent_ledger("dev-001", tlon_paths=tlon_paths)

        assert summary["by_purpose"]["unknown"]["calls"] == 1

    async def test_member_without_ledger_reads_zero(self, tmp_path: Path) -> None:
        summary = await collect_agent_ledger("ghost", tlon_paths=TlonPaths(home=tmp_path))

        assert summary["calls"] == 0
        assert summary["total_tokens"] == 0
        assert summary["by_purpose"] == {}
        # Read-only discipline: the query creates nothing.
        assert not (tmp_path / "ghost").exists()


class TestCollectLineageLedgers:
    async def test_aggregates_members_by_prototype(self, tmp_path: Path) -> None:
        tlon_paths = TlonPaths(home=tmp_path)
        yaml_save(AgentConfig(id="dev-001", name="Dev 1", prototype="developer"), tmp_path)
        yaml_save(AgentConfig(id="dev-002", name="Dev 2", prototype="developer"), tmp_path)
        yaml_save(AgentConfig(id="scribe", name="Scribe"), tmp_path)
        await _write_records(
            tlon_paths.agent_ledger_file("dev-001"),
            [_record(prompt_tokens=10, completion_tokens=2)],
        )
        await _write_records(
            tlon_paths.agent_ledger_file("dev-002"),
            [
                _record(prompt_tokens=20, completion_tokens=4),
                _record(purpose="summarize", prompt_tokens=5, completion_tokens=1),
            ],
        )

        lineages = await collect_lineage_ledgers(tlon_paths=tlon_paths)

        developer = lineages["developer"]
        assert developer["prototype"] == "developer"
        assert developer["members"] == ["dev-001", "dev-002"]
        assert developer["calls"] == 3
        assert developer["prompt_tokens"] == 35
        assert developer["completion_tokens"] == 7
        assert developer["total_tokens"] == 42
        # A member with no ledger still shows up, counting zero.
        manual = lineages["manual"]
        assert manual["prototype"] is None
        assert manual["members"] == ["scribe"]
        assert manual["calls"] == 0
