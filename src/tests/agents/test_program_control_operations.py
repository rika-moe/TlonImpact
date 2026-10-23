"""Local conversation completion is composable ordinary data."""

from tlon.agents.program_driver import ProgramDriver
from tlon.agents.program_store import ProgramStore
from tlon.operations.core import CallContext, OperationExecutor, OperationRegistry
from tlon.operations.dsl import DSLAdapter
from tlon.operations.lifecycle import make_lifecycle_operations
from tlosure.modules import ModulePackage


async def test_nested_finish_returns_to_caller_without_global_scheduling() -> None:
    store = ProgramStore(None)
    adapter = DSLAdapter(
        OperationExecutor(
            OperationRegistry(make_lifecycle_operations(None)),
            CallContext("member", "root", "call"),
            store,
        )
    )
    package = ModulePackage.from_sources(
        {
            "work.tlon": """(ns work (:export handle))
    (defn finish [] (finish-turn {}))
    (defn handle [event] {:local (finish) :continued true})"""
        },
        adapter.builtins(),
    )
    result = await ProgramDriver(store, "root", adapter.builtins(), operations=adapter).run_entry(
        package.entry("work/handle"), {}, fuel=1000
    )
    assert result == {"local": {"finished": True}, "continued": True}
    assert not OperationRegistry(make_lifecycle_operations(None)).get("finish-turn").effects
