"""Two entrances preserve private member identity and real on-disk state."""

from pathlib import Path
from unittest.mock import patch

import pytest

from tlon.base.paths import TlonPaths
from tlon.operations.adapters import OperationTool
from tlon.operations.core import CallContext, OperationExecutor, OperationRegistry
from tlon.operations.dsl import DSLAdapter
from tlon.operations.identity import make_identity_operations
from tlon.operations.schema import UnknownOutcomeError
from tlosure.builtins import BuiltinCall
from tlosure.edn import Keyword


def _bound(paths: TlonPaths, member: str) -> OperationExecutor:
    return OperationExecutor(
        OperationRegistry(make_identity_operations(paths, member)),
        CallContext(member, "run", f"call-{member}"),
    )


def test_identity_contract_only_exposes_persona() -> None:
    assert [operation.name for operation in make_identity_operations(None, "")] == [
        "persona-update"
    ]


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
    with (
        patch.object(Path, "write_text", side_effect=OSError("disk failure")),
        pytest.raises(UnknownOutcomeError) as failure,
    ):
        await OperationTool(alice, "persona-update").execute_context(
            {"file": "identity", "content": "自己"}
        )
    assert failure.value.data == {
        "outcome": "unknown",
        "call_id": "call-alice",
        "retry_safe": False,
    }
    invalid = await OperationTool(alice, "persona-update").execute_context(
        {"file": "rules", "content": "x"}
    )
    assert not invalid.success and "InputError" in invalid.error
