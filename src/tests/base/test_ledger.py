"""Tests for the metabolic ledger (ADR 0014).

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
    config = LLMConfig(model_name=model_name, max_retries=1)
    return LLMClient(config, agent_id=agent_id, tlon_paths=tlon_paths)


def _mock_litellm(monkeypatch: pytest.MonkeyPatch, response: Any) -> None:
    # The preflight (ADR 0016) requires the provider key env var; litellm
    # itself is mocked, so the value never leaves the test.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
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
    model: str | None = "openai/gpt-test",
) -> dict[str, Any]:
    return {
        "ts": "2026-09-13T00:00:00+00:00",
        "event": "llm_call",
        "model": model,
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


class TestCacheTokenRecording:
    """Prompt-cache accounting (prompt prefix caching): litellm normalizes
    every provider's cache fields into ``prompt_tokens_details`` — the
    ledger records them so hit rates are measurable."""

    def _response_with_details(self, details: dict[str, Any] | None) -> SimpleNamespace:
        message = SimpleNamespace(content="hi", tool_calls=None, reasoning_content=None)
        choice = SimpleNamespace(message=message, finish_reason="stop")
        usage = SimpleNamespace(
            model_dump=lambda: {
                "prompt_tokens": 200,
                "completion_tokens": 5,
                "prompt_tokens_details": details,
            }
        )
        return SimpleNamespace(choices=[choice], usage=usage)

    async def _sole_record(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, response: Any
    ) -> dict[str, Any]:
        tlon_paths = TlonPaths(home=tmp_path)
        client = _client(tlon_paths)
        _mock_litellm(monkeypatch, response)
        await client.chat(system_prompt="sys", messages=[{"role": "user", "content": "hi"}])
        ledger_file = tmp_path / "dev-001" / "ledger.jsonl"
        return json.loads(ledger_file.read_text().splitlines()[0])

    async def test_cached_and_write_tokens_recorded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        response = self._response_with_details({"cached_tokens": 150, "cache_write_tokens": 40})

        record = await self._sole_record(tmp_path, monkeypatch, response)

        assert record["cached_tokens"] == 150
        assert record["cache_write_tokens"] == 40

    async def test_details_none_records_zero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        record = await self._sole_record(tmp_path, monkeypatch, self._response_with_details(None))

        assert record["cached_tokens"] == 0
        assert record["cache_write_tokens"] == 0

    async def test_details_absent_records_zero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        record = await self._sole_record(tmp_path, monkeypatch, _fake_litellm_response())

        assert record["cached_tokens"] == 0
        assert record["cache_write_tokens"] == 0


class TestReadLedger:
    async def test_torn_tail_line_is_skipped(self, tmp_path: Path) -> None:
        path = tmp_path / "dev-001" / "ledger.jsonl"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(_record()) + "\n" + '{"ts": "2026', encoding="utf-8")

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
        await _write_records(tlon_paths.agent_ledger_file("dev-001"), [_record(purpose=None)])

        summary = await collect_agent_ledger("dev-001", tlon_paths=tlon_paths)

        assert summary["by_purpose"]["unknown"]["calls"] == 1

    async def test_member_without_ledger_reads_zero(self, tmp_path: Path) -> None:
        summary = await collect_agent_ledger("ghost", tlon_paths=TlonPaths(home=tmp_path))

        assert summary["calls"] == 0
        assert summary["total_tokens"] == 0
        assert summary["by_purpose"] == {}
        assert summary["by_model"] == {}
        # Read-only discipline: the query creates nothing.
        assert not (tmp_path / "ghost").exists()

    async def test_non_llm_call_records_are_skipped(self, tmp_path: Path) -> None:
        """The ledger is multi-event (ADR 0024 persona_update audit trail
        shares the file); metabolic folds count only llm_call records."""
        tlon_paths = TlonPaths(home=tmp_path)
        persona_record = {
            "ts": "2026-10-07T00:00:00+00:00",
            "event": "persona_update",
            "agent_id": "dev-001",
            "file": "identity",
            "sha256": "ab" * 32,
            "bytes": 42,
        }
        await _write_records(
            tlon_paths.agent_ledger_file("dev-001"),
            [
                _record(purpose="think", prompt_tokens=10, completion_tokens=2, duration_ms=100),
                persona_record,
                _record(purpose="summarize", prompt_tokens=5, completion_tokens=1, duration_ms=50),
            ],
        )

        summary = await collect_agent_ledger("dev-001", tlon_paths=tlon_paths)

        assert summary["calls"] == 2
        assert summary["prompt_tokens"] == 15
        assert summary["completion_tokens"] == 3
        assert summary["total_tokens"] == 18
        assert summary["duration_ms"] == 150
        assert list(summary["by_purpose"]) == ["think", "summarize"]
        assert "unknown" not in summary["by_model"]


class TestCollectAgentLedgerByModel:
    """issue : per-model split, same shape as by_purpose — observing
    how mixed-model bloodlines perform."""

    async def test_mixed_models_split(self, tmp_path: Path) -> None:
        tlon_paths = TlonPaths(home=tmp_path)
        await _write_records(
            tlon_paths.agent_ledger_file("dev-001"),
            [
                _record(
                    model="moonshot/kimi-k2.5",
                    prompt_tokens=10,
                    completion_tokens=2,
                    duration_ms=100,
                    cpu_ms=4,
                ),
                _record(
                    model="deepseek/deepseek-chat",
                    prompt_tokens=20,
                    completion_tokens=4,
                    duration_ms=200,
                    cpu_ms=8,
                ),
                _record(
                    model="moonshot/kimi-k2.5",
                    prompt_tokens=5,
                    completion_tokens=1,
                    duration_ms=50,
                    cpu_ms=2,
                ),
            ],
        )

        summary = await collect_agent_ledger("dev-001", tlon_paths=tlon_paths)

        assert summary["calls"] == 3
        kimi = summary["by_model"]["moonshot/kimi-k2.5"]
        assert kimi["calls"] == 2
        assert kimi["prompt_tokens"] == 15
        assert kimi["total_tokens"] == 18
        assert kimi["duration_ms"] == 150
        assert kimi["cpu_ms"] == 6
        deepseek = summary["by_model"]["deepseek/deepseek-chat"]
        assert deepseek["calls"] == 1
        assert deepseek["total_tokens"] == 24

    async def test_record_without_model_folds_into_unknown(self, tmp_path: Path) -> None:
        tlon_paths = TlonPaths(home=tmp_path)
        await _write_records(tlon_paths.agent_ledger_file("dev-001"), [_record(model=None)])

        summary = await collect_agent_ledger("dev-001", tlon_paths=tlon_paths)

        assert summary["by_model"]["unknown"]["calls"] == 1


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
        assert manual["by_model"] == {}

    async def test_lineage_merges_members_by_model(self, tmp_path: Path) -> None:
        """A bloodline's by_model merges every member's per-model split."""
        tlon_paths = TlonPaths(home=tmp_path)
        yaml_save(AgentConfig(id="dev-001", name="Dev 1", prototype="developer"), tmp_path)
        yaml_save(AgentConfig(id="dev-002", name="Dev 2", prototype="developer"), tmp_path)
        await _write_records(
            tlon_paths.agent_ledger_file("dev-001"),
            [_record(model="moonshot/kimi-k2.5", prompt_tokens=10, completion_tokens=2)],
        )
        await _write_records(
            tlon_paths.agent_ledger_file("dev-002"),
            [
                _record(model="moonshot/kimi-k2.5", prompt_tokens=20, completion_tokens=4),
                _record(model="deepseek/deepseek-chat", prompt_tokens=5, completion_tokens=1),
            ],
        )

        lineages = await collect_lineage_ledgers(tlon_paths=tlon_paths)

        by_model = lineages["developer"]["by_model"]
        assert by_model["moonshot/kimi-k2.5"]["calls"] == 2
        assert by_model["moonshot/kimi-k2.5"]["total_tokens"] == 36
        assert by_model["deepseek/deepseek-chat"]["calls"] == 1
        assert by_model["deepseek/deepseek-chat"]["prompt_tokens"] == 5
