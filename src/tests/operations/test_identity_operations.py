"""Two entrances preserve private member identity and real on-disk state."""

from pathlib import Path
from unittest.mock import patch

import pytest

from tlon.base.paths import TlonPaths
from tlon.operations.adapters import DSLAdapter, OperationTool
from tlon.operations.core import CallContext, OperationExecutor, OperationRegistry
from tlon.operations.identity import make_identity_operations
from tlon.operations.schema import InputError, json_value
from tlosure.builtins import BuiltinCall
from tlosure.edn import Keyword


def _bound(paths: TlonPaths, member: str) -> OperationExecutor:
    return OperationExecutor(
        OperationRegistry(make_identity_operations(paths, member)),
        CallContext(member, "run", f"call-{member}"),
    )


async def test_real_contacts_two_members_two_entrances(tmp_path: Path) -> None:
    paths = TlonPaths(home=tmp_path)
    alice, bob = _bound(paths, "alice"), _bound(paths, "bob")
    adapter = DSLAdapter(alice)
    args = {
        Keyword("name"): "小林",
        Keyword("address"): "lin@tlon.internal",
        Keyword("description"): "靠谱",
    }
    result = await adapter.handle(
        BuiltinCall("contacts-upsert", adapter.builtins()["contacts-upsert"], [args], {})
    )
    assert json_value(result, edn_keys=True)["action"] == "added"
    read = await OperationTool(alice, "contacts-read").execute_context({})
    assert read.success and read.data["entries"] == 1 and "靠谱" in read.data["content"]
    other = await OperationTool(bob, "contacts-read").execute_context({})
    assert other.success and other.data["entries"] == 0
    assert not paths.agent_contacts_file("bob").exists()
    invalid = await OperationTool(alice, "contacts-upsert").execute_context(
        {"name": "x", "address": "y", "description": "z", "member_id": "bob"}
    )
    assert not invalid.success
    with pytest.raises(InputError):
        await adapter.handle(
            BuiltinCall(
                "contacts-read",
                adapter.builtins()["contacts-read"],
                [{Keyword("member_id"): "bob"}],
                {},
            )
        )


async def test_persona_two_entrances_and_write_failure_receipt(tmp_path: Path) -> None:
    paths = TlonPaths(home=tmp_path)
    alice = _bound(paths, "alice")
    adapter = DSLAdapter(alice)
    await adapter.handle(
        BuiltinCall(
            "persona-update",
            adapter.builtins()["persona-update"],
            [{Keyword("file"): "soul", Keyword("content"): "认真"}],
            {},
        )
    )
    assert paths.agent_soul_file("alice").read_text() == "认真"
    assert not paths.agent_soul_file("bob").exists()
    with patch.object(Path, "write_text", side_effect=OSError("disk failure")):
        result = await OperationTool(alice, "persona-update").execute_context(
            {"file": "identity", "content": "自己"}
        )
    assert not result.success
    assert result.data == {"outcome": "unknown", "call_id": "call-alice", "retry_safe": False}
    invalid = await OperationTool(alice, "persona-update").execute_context(
        {"file": "rules", "content": "x"}
    )
    assert not invalid.success and "InputError" in invalid.error
