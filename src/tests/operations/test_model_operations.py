"""The two model entry points keep their owning client's prompt and ledger."""

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from tlon.base.config import LLMConfig
from tlon.base.llm_client import LLMClient, LLMResponse
from tlon.base.paths import TlonPaths
from tlon.operations.adapters import DSLAdapter, OperationTool
from tlon.operations.core import CallContext, OperationExecutor, OperationRegistry
from tlon.operations.model import make_model_operations
from tlon.operations.schema import UnknownOutcomeError
from tlosure.edn import loads
from tlosure.eval import Evaluator


async def test_model_entries_charge_only_the_bound_member(tmp_path: Path) -> None:
    paths = TlonPaths(home=tmp_path)
    for member in ("a", "b"):
        client = LLMClient(LLMConfig(model_name="openai/test"), agent_id=member, tlon_paths=paths)
        client._chat_with_retries = AsyncMock(
            return_value=LLMResponse(
                content=member,
                tool_calls=[],
                usage={"prompt_tokens": 7, "completion_tokens": 3},
            )
        )
        system = AsyncMock(return_value=f"persona:{member}")
        executor = OperationExecutor(
            OperationRegistry(make_model_operations(client, system)),
            CallContext(member, f"run:{member}", f"call:{member}"),
        )
        if member == "a":
            adapter = DSLAdapter(executor)
            result = await Evaluator(adapter.builtins(), 1000, 100).eval_async(
                loads('(model-call {:prompt "input" :max_tokens 5})'), {}, 0, adapter.handle
            )
            assert result["content"] == member
        else:
            result = await OperationTool(executor, "model-call").execute_context(
                {"prompt": "input", "max_tokens": 5}
            )
            assert result.success and result.data["content"] == member
        system.assert_awaited_once()
        records = [
            json.loads(line) for line in paths.agent_ledger_file(member).read_text().splitlines()
        ]
        assert len(records) == 1
        assert records[0]["completion_tokens"] == 3
        assert records[0]["session_id"] == f"run:{member}"
        assert records[0]["model"] == "openai/test"
    assert len(paths.agent_ledger_file("a").read_text().splitlines()) == 1


async def test_model_schema_rejects_identity_and_boolean_budget() -> None:
    client = AsyncMock()
    executor = OperationExecutor(
        OperationRegistry(make_model_operations(client, AsyncMock())), CallContext("a", "r", "c")
    )
    tool = OperationTool(executor, "model-call")
    for arguments in (
        {"prompt": "x", "max_tokens": True},
        {"prompt": "x", "max_tokens": 2, "member_id": "b"},
    ):
        result = await tool.execute_context(arguments)
        assert not result.success and "InputError" in result.error
    client.chat.assert_not_awaited()


async def test_model_lost_response_reports_unknown_call_identity() -> None:
    client = AsyncMock()
    client.chat.side_effect = OSError("lost")
    executor = OperationExecutor(
        OperationRegistry(make_model_operations(client, AsyncMock(return_value="system"))),
        CallContext("a", "r", "stable"),
    )
    with pytest.raises(UnknownOutcomeError) as failure:
        await executor.call("model-call", {"prompt": "x", "max_tokens": 1})
    assert failure.value.data == {"call_id": "stable", "outcome": "unknown", "retry_safe": False}
