"""Dependency approval invalidation stays within its dependency closure."""

from pathlib import Path

from tests.agents.capabilities.test_spec import PURE
from tlon.agents.capabilities.bridge import load_capability_catalog
from tlon.agents.capabilities.staging import approve_capability, write_staging_capability
from tlon.base.paths import TlonPaths
from tlon.operations.adapters import OperationTool
from tlon.operations.core import CallContext, OperationExecutor, OperationRegistry
from tlon.operations.dsl import DSLAdapter
from tlosure.edn import loads
from tlosure.eval import DEFAULT_FUEL, DEFAULT_MAX_DEPTH, Evaluator


def approve(paths: TlonPaths, text: str, name: str) -> None:
    write_staging_capability(text, tlon_paths=paths)
    approve_capability(name, paths)


def test_dependency_change_rejects_upstream_until_reapproved(tmp_path: Path) -> None:
    paths = TlonPaths(home=tmp_path)
    approve(paths, PURE, "shout")
    parent = (
        PURE.replace('"shout"', '"twice"')
        .replace('(str (get input :text) "!")', "(shout {:text (shout input)})")
        .replace(':expect "hey!"', ':expect "hey!!"')
    )
    approve(paths, parent, "twice")
    independent = PURE.replace('"shout"', '"other"')
    approve(paths, independent, "other")
    before = load_capability_catalog(paths)
    assert (
        before.definitions["twice"].dependency_ids["shout"]
        == before.definitions["shout"].definition_id
    )
    path = paths.capabilities_dir() / "shout/capability.tlon"
    path.write_text(PURE.replace('"!"', '"?"').replace('"hey!"', '"hey?"'))
    assert set(load_capability_catalog(paths).definitions) == {"other"}
    approve_capability("shout", paths)
    assert set(load_capability_catalog(paths).definitions) == {"shout", "other"}
    parent_path = paths.capabilities_dir() / "twice/capability.tlon"
    parent_path.write_text(parent.replace('"hey!!"', '"hey??"'))
    approve_capability("twice", paths)
    assert set(load_capability_catalog(paths).definitions) == {"shout", "twice", "other"}


async def test_same_composite_is_dsl_and_tool_operation(tmp_path: Path) -> None:
    paths = TlonPaths(home=tmp_path)
    approve(paths, PURE, "shout")
    catalog = load_capability_catalog(paths)
    executor = OperationExecutor(
        OperationRegistry(catalog.operations), CallContext("member", "run", "call")
    )
    builtin = DSLAdapter(executor).builtins()
    assert (
        Evaluator(builtin, DEFAULT_FUEL, DEFAULT_MAX_DEPTH).eval(
            loads('(shout {:text "hey"})'), {}, 0
        )
        == "hey!"
    )
    result = await OperationTool(executor, "shout").execute_context({"text": "hey"})
    assert result.success and result.data == "hey!"


def test_bad_artifact_isolated_and_deleted_dependency_propagates(tmp_path: Path) -> None:
    paths = TlonPaths(home=tmp_path)
    approve(paths, PURE, "shout")
    bad = paths.capabilities_dir() / "broken"
    bad.mkdir()
    (bad / "capability.tlon").write_text("{:version")
    catalog = load_capability_catalog(paths)
    assert set(catalog.definitions) == {"shout"} and "broken" in catalog.errors
