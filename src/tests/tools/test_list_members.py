"""Public member directory tool: thin, nonblocking, three-field projection."""

import threading
from typing import Any

from tlon.organization.mail.resolver import MemberAddress
from tlon.tools.agents.list_members import ListMembersTool


class _DirectoryStub:
    def __init__(self) -> None:
        self.called_thread: int | None = None

    def list_members(self) -> list[MemberAddress]:
        self.called_thread = threading.get_ident()
        records: list[dict[str, Any]] = [
            {"id": "adam", "name": "Adam", "address": "adam@tlon.internal", "role": "guardian"},
            {"id": "rika", "name": "rika", "address": "rika@tlon.internal", "type": "human"},
            {
                "id": "worker",
                "name": "工作者",
                "address": "worker@tlon.internal",
                "state": "sleeping",
            },
        ]
        return records  # type: ignore[return-value]


async def test_public_projection_and_nonblocking_query() -> None:
    directory = _DirectoryStub()
    tool = ListMembersTool(directory=directory)

    result = await tool.execute()

    assert tool.name == "listMembers"
    assert result.success is True
    assert result.message == "共 3 个成员"
    assert result.data == [
        {"id": "adam", "name": "Adam", "address": "adam@tlon.internal"},
        {"id": "rika", "name": "rika", "address": "rika@tlon.internal"},
        {"id": "worker", "name": "工作者", "address": "worker@tlon.internal"},
    ]
    assert directory.called_thread != threading.get_ident()
