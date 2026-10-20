"""Host clock operation has no execution-device dependency."""

from datetime import datetime

from tlon.operations.adapters import OperationTool
from tlon.operations.core import CallContext, OperationExecutor, OperationRegistry
from tlon.operations.time import time_operation_definitions


async def test_time_contract():
    executor = OperationExecutor(
        OperationRegistry(time_operation_definitions()), CallContext("a", "r", "c")
    )
    tool = OperationTool(executor, "time-now")
    result = await tool.execute_context({})
    assert result.success
    parsed = datetime.fromisoformat(result.data["iso"])
    assert parsed.utcoffset() is not None
    assert int(parsed.timestamp()) == result.data["unix"]
    assert result.data["timezone"]
    assert result.data["weekday"]
    assert not (await tool.execute_context({"extra": True})).success
