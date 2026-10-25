"""Empty distillation is success; malformed committed model output is not memory."""

from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock

import pytest

from tests.memory.test_run_distillation import _completed_run, _journal, _manager
from tlon.agents.program_store import ProgramStore
from tlon.base.config import LLMConfig
from tlon.base.execution import ExecutionScope, bind_execution
from tlon.base.llm_client import LLMClient, LLMResponse
from tlon.base.maintenance import MaintenanceCheckpointError
from tlon.memory.distiller import DistillationResponseError, Distiller
from tlon.memory.models import RunProjection, is_empty_boot_run


def distiller() -> Distiller:
    return Distiller(
        LLMConfig(model_name="openai/test", max_output_tokens=100, context_window=10000)
    )


@pytest.mark.parametrize(
    "content,finish",
    [
        ('{"claims":[]} extra explanation', "stop"),
        ('```json\n{"claims":[]}\n```', "stop"),
        ('{"claims":[]}', "length"),
        ("", "stop"),
        ('{"claims":[{"text":" ","importance":0.5}]}', "stop"),
        ('{"claims":[{"text":"fact","importance":2}]}', "stop"),
        ('{"claims":[{"text":5,"importance":0.5}]}', "stop"),
        ('{"claims":[],"extra":true}', "stop"),
        ("{}", "stop"),
    ],
)
async def test_invalid_response_never_becomes_empty_success(content: str, finish: str) -> None:
    worker = distiller()
    worker.llm_client.chat = AsyncMock(
        return_value=LLMResponse(content=content, finish_reason=finish)
    )
    with pytest.raises(DistillationResponseError):
        await worker.distill([RunProjection("task", "remember facts")])


async def test_valid_empty_json_requests_structured_response() -> None:
    worker = distiller()
    worker.llm_client.chat = AsyncMock(return_value=LLMResponse(content='{"claims":[]}'))
    assert await worker.distill([RunProjection("task", "hello")]) == []
    assert worker.llm_client.chat.await_args.kwargs["response_format"] == {"type": "json_object"}


async def test_bad_durable_response_preserves_sources_and_replays_without_new_charge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = _journal(tmp_path)
    await _completed_run(journal, "source", "important task")
    provider = AsyncMock(
        return_value=LLMResponse(
            content='{"claims":[]} followed by explanation', usage={"completion_tokens": 4}
        )
    )
    monkeypatch.setattr(LLMClient, "_chat_accounted", provider)
    store = ProgramStore(tmp_path / "budget.sqlite", build_id="test")
    roots = []
    for index in range(2):
        manager = _manager(tmp_path, distillation_window=1)
        manager.distiller = distiller()
        with (
            bind_execution(ExecutionScope(store, f"outer-{index}", 1000, "output")),
            pytest.raises(MaintenanceCheckpointError, match="invalid distillation"),
        ):
            await manager.maybe_distill_completed_runs(journal)
        assert manager._pending_batch is not None
        roots.append("maintenance:distill:" + manager._pending_batch["id"])
        assert manager._consumed == set()
        assert await manager.claims.get_all_claims() == []
        assert manager.maintenance_error is not None
    assert roots[0] == roots[1]
    assert provider.await_count == 1
    assert provider.await_args.kwargs["response_format"] == {"type": "json_object"}
    assert store.budget_usage(roots[0])["used"] == 4


async def test_inert_boot_is_consumed_without_model_but_effectful_boot_is_distilled(
    tmp_path: Path,
) -> None:
    journal = _journal(tmp_path)

    inert = "empty:handler:boot"
    assert journal._program_store is not None
    journal._program_store.ensure_entry(inert, "{}", "{:payload nil}", {})
    journal._program_store.complete_entry(inert, None)
    manager = _manager(tmp_path, distillation_window=1)
    assert await manager.maybe_distill_completed_runs(journal) is None
    assert inert in manager._consumed
    assert manager.distiller is not None
    cast(AsyncMock, manager.distiller.distill).assert_not_awaited()
    effectful = "effectful:handler:boot"
    await _completed_run(journal, effectful, "", reply="actual model evidence")
    assert not is_empty_boot_run(await journal.read_events(effectful), effectful)
    assert await manager.maybe_distill_completed_runs(journal) == 1
    cast(AsyncMock, manager.distiller.distill).assert_awaited_once()


@pytest.mark.parametrize(
    "effect",
    [
        {"type": "llm/response", "response": {"content": None, "tool_calls": []}},
        {"type": "tool/result", "result": {"success": True, "data": {}}},
        {"type": "program/invocation-pending", "invocation": {"name": "mail-send"}},
    ],
)
def test_boot_with_model_or_tool_evidence_is_never_skipped(effect: dict[str, object]) -> None:
    events = [
        {"type": "run/start", "trigger_content": "null"},
        effect,
        {"type": "run/end", "result": None},
    ]
    assert not is_empty_boot_run(events, "boot-event:handler:boot")
