"""Member request caps and durable program allowances are independent."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from tests.program_helpers import make_program
from tlon.agents.member_runtime import MemberRuntime
from tlon.agents.vocabulary import make_member_builtins
from tlon.base.config import AgentConfig, LLMConfig
from tlon.base.events.event_bus import EventBus
from tlon.base.paths import TlonPaths
from tlosure.edn import loads


@pytest.mark.parametrize("invalid", [0, -1, True, 1.5])
def test_program_allowance_must_be_a_positive_integer(invalid: Any) -> None:
    with pytest.raises(ValidationError):
        AgentConfig(id="member", name="Member", program_token_budget=invalid)


@pytest.mark.parametrize("budget", [None, 12000])
async def test_entry_freezes_program_allowance_independently_of_request_cap(
    tmp_path: Path, budget: int | None
) -> None:
    config = AgentConfig(id="member", name="Member", llm=LLMConfig(max_tokens=73))
    if budget is not None:
        config.program_token_budget = budget
    expected = 100000 if budget is None else budget
    member = MemberRuntime(
        member_id=config.id,
        name=config.name,
        event_bus=EventBus(),
        spec=make_program(),
        builtins=make_member_builtins(),
        tlon_paths=TlonPaths(home=tmp_path),
    )
    member._services = cast(Any, SimpleNamespace(config=config))
    member._program_model_contexts = AsyncMock(return_value={})
    member._run_entry_record = AsyncMock(return_value=True)
    handler = member.spec.handlers[0]
    try:
        assert member._maintenance_execution("audit").token_limit == expected
        await member._run_handler_body(handler, None, run_id="entry")
        record = member._program_store.get_run("entry")
        assert record is not None and record.package_edn is not None
        assert loads(record.package_edn)["token_budget"] == expected
        config.program_token_budget = 42
        assert config.llm is not None
        config.llm.max_tokens = 1
        await member._run_handler_body(handler, None, run_id="entry")
        restored = member._run_entry_record.await_args.args[0]
        assert loads(restored.package_edn)["token_budget"] == expected
        assert member._program_model_contexts.await_count == 1
    finally:
        member._program_store.close()
