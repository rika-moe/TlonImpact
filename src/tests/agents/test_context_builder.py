"""Member context consists of ordinary reads, independent of workflow configuration."""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from tlon.agents.context_builder import EXTERNAL_MAIL_MARKER, ContextBuilder
from tlon.base.config import AgentConfig
from tlon.base.paths import TlonPaths
from tlon.operations.context import member_context_operation
from tlon.operations.core import CallContext
from tlon.operations.schema import OperationError
from tlon.organization.mail.mail import Mail
from tlon.organization.mail.mailbox import Mailbox
from tlon.tasks.manager import TaskManager


async def test_context_reads_owned_materials_and_explicit_history_limit(tmp_path: Path) -> None:
    paths = TlonPaths(home=tmp_path)
    mailbox = Mailbox(paths.mail_database_file(), "a")
    incoming = Mail.create(
        from_address="person@example.com", to=["a@tlon.internal"], content="question"
    )
    await mailbox.append_inbox(incoming)
    memory = AsyncMock()
    memory.build_context_blocks.return_value = []
    builder = ContextBuilder(
        AgentConfig(id="a", name="A"),
        mailbox,
        memory,
        TaskManager(agent_id="a", tlon_paths=paths),
        "a@tlon.internal",
        tlon_paths=paths,
    )
    operation = member_context_operation(builder, lambda: ["mail-send"])
    result = await operation.handler(
        CallContext("a", "run", "read"), {"query": "topic", "history_limit": 1}
    )
    memory.build_context_blocks.assert_awaited_once_with("topic")
    assert result["tools"] == ["mail-send"]
    assert result["thread_recap"] == ""
    assert result["mail_history"][0]["role"] == "user"
    assert EXTERNAL_MAIL_MARKER in result["mail_history"][0]["content"]
    assert await builder.build_mail_history(0) == []


async def test_unbound_context_reader_refuses_execution() -> None:
    with pytest.raises(OperationError, match="not bound"):
        await member_context_operation().handler(CallContext("a", "r", "c"), {})


async def test_context_read_failure_is_not_hidden_as_an_empty_success() -> None:
    reader = AsyncMock()
    reader.build_knowledge_context.side_effect = RuntimeError("read failed")
    with pytest.raises(RuntimeError, match="read failed"):
        await member_context_operation(reader, lambda: []).handler(CallContext("a", "r", "c"), {})


async def test_contacts_are_read_on_demand_without_invalidating_system_prompt(
    tmp_path: Path,
) -> None:
    paths = TlonPaths(home=tmp_path)
    builder = ContextBuilder(
        AgentConfig(id="a", name="A"),
        Mailbox(paths.mail_database_file(), "a"),
        AsyncMock(),
        TaskManager(agent_id="a", tlon_paths=paths),
        "a@tlon.internal",
        tlon_paths=paths,
    )
    before = await builder.get_system_prompt()
    signature = builder.persona_mtime_signature(builder.persona_files())
    contacts = paths.agent_contacts_file("a")
    contacts.parent.mkdir(parents=True, exist_ok=True)
    contacts.write_text("private acquaintance one", encoding="utf-8")
    assert await builder.get_system_prompt() is before
    contacts.write_text("private acquaintance two", encoding="utf-8")
    assert await builder.get_system_prompt() is before
    assert builder.persona_mtime_signature(builder.persona_files()) == signature
    assert "CONTACTS.md" in before
    assert "file-read" in before and "file-edit" in before and "file-write" in before
    assert "private acquaintance" not in before
    assert contacts not in builder.persona_files()
