"""Evolution authority, shared projections and publication failure boundaries."""

from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

from tlon.agents.prototypes.staging import list_staging_prototypes, write_staging_prototype
from tlon.base.paths import TlonPaths
from tlon.evolution.list_staging import StagingCatalog
from tlon.evolution.publication import publish_directory
from tlon.evolution.write_prototype import PrototypeWriter
from tlon.operations.adapters import OperationTool
from tlon.operations.core import CallContext, OperationExecutor, OperationRegistry
from tlon.operations.dsl import DSLAdapter
from tlon.operations.evolution import make_evolution_operations
from tlon.operations.schema import OperationError, UnknownOutcomeError, json_value
from tlosure.builtins import BuiltinCall
from tlosure.edn import Keyword

BUNDLE_BEHAVIOR = "{:version 2 :handlers {:mail-delivered work.reply/handle} :fuel 1000}"
BUNDLE_MODULES = {
    "work/reply.tlon": "(ns work.reply (:require [work.helper :as h]) (:export handle)) (defn handle [event] (h/result))",
    "work/helper.tlon": '(ns work.helper (:export result)) (defn result [] {:result "ok"})',
}

_SPEC = "{:version 2 :handlers {:boot work/handle}}"
_MODULES = {"work.tlon": "(ns work (:export handle)) (defn handle [event] {})"}


def _bound(paths: TlonPaths) -> OperationExecutor:
    return OperationExecutor(
        OperationRegistry(
            make_evolution_operations(
                "alice",
                writer=PrototypeWriter(
                    "alice", lambda *a, **kw: write_staging_prototype(*a, tlon_paths=paths, **kw)
                ),
                staging=StagingCatalog(lambda: list_staging_prototypes(paths)),
            )
        ),
        CallContext("alice", "run", "call-alice"),
    )


async def test_two_entrances_host_author_and_human_trust(tmp_path: Path) -> None:
    paths = TlonPaths(home=tmp_path)
    executor = _bound(paths)
    adapter = DSLAdapter(executor)
    args = {
        Keyword("behavior"): BUNDLE_BEHAVIOR,
        Keyword("modules"): BUNDLE_MODULES,
        Keyword("display_name"): "空启动",
    }
    result = await adapter.handle(
        BuiltinCall("prototype-write", adapter.builtins()["prototype-write"], [args], {})
    )
    proposal = json_value(result, edn_keys=True)
    listed = await OperationTool(executor, "prototype-staging-list").execute_context({})
    assert listed.success
    assert listed.data[0]["name"] == proposal["name"]
    assert listed.data[0]["author"] == "alice"
    assert proposal["trusted"] is False and proposal["status"] == "pending_approval"
    assert not paths.prototypes_dir().exists()
    forged = await OperationTool(executor, "prototype-write").execute_context(
        {"behavior": _SPEC, "display_name": "x", "author_id": "bob"}
    )
    assert not forged.success and "InputError" in forged.error
    with pytest.raises(OperationError):
        await executor.call("prototype-staging-list", {}, CallContext("bob", "run", "forged"))
    assert len(list_staging_prototypes(paths)) == 1


async def test_member_can_propose_through_tool_and_dsl() -> None:
    service = Mock()
    service.execute = AsyncMock(
        return_value={
            "name": "proposal",
            "display_name": "x",
            "parent": None,
            "generation": 0,
            "trusted": False,
            "status": "pending_approval",
        }
    )
    executor = OperationExecutor(
        OperationRegistry(make_evolution_operations("born", writer=service)),
        CallContext("born", "run", "call"),
    )
    args = {"behavior": BUNDLE_BEHAVIOR, "modules": BUNDLE_MODULES, "display_name": "x"}
    result = await OperationTool(executor, "prototype-write").execute_context(args)
    assert result.success
    adapter = DSLAdapter(executor)
    await adapter.handle(
        BuiltinCall(
            "prototype-write",
            adapter.builtins()["prototype-write"],
            [{Keyword(k): v for k, v in args.items()}],
            {},
        )
    )
    assert service.execute.await_count == 2


def test_staging_fsync_failure_leaves_no_partial_package(tmp_path: Path) -> None:
    paths = TlonPaths(home=tmp_path)
    with (
        patch("tlon.evolution.publication.os.fsync", side_effect=OSError("disk failure")),
        pytest.raises(OSError, match="disk failure"),
    ):
        write_staging_prototype(_SPEC, modules=_MODULES, display_name="x", tlon_paths=paths)
    assert list_staging_prototypes(paths) == []
    assert list(tmp_path.glob(".proposal-*")) == []
    assert not any(p.name == "behavior.tlon" for p in tmp_path.rglob("behavior.tlon"))


def test_publication_rejects_existing_even_empty_target(tmp_path: Path) -> None:
    target = tmp_path / "staging" / "existing"
    target.mkdir(parents=True)
    with pytest.raises(FileExistsError):
        publish_directory(target, {"payload": "new"})
    assert list(target.iterdir()) == []
    assert list(tmp_path.glob(".proposal-*")) == []


async def test_write_io_failure_is_unknown_with_host_call_id(tmp_path: Path) -> None:
    executor = _bound(TlonPaths(home=tmp_path))
    with (
        patch("tlon.evolution.publication.os.fsync", side_effect=OSError("disk failure")),
        pytest.raises(UnknownOutcomeError) as failure,
    ):
        await OperationTool(executor, "prototype-write").execute_context(
            {"behavior": BUNDLE_BEHAVIOR, "modules": BUNDLE_MODULES, "display_name": "x"}
        )
    assert failure.value.data == {
        "outcome": "unknown",
        "call_id": "call-alice",
        "retry_safe": False,
    }


async def test_error_after_publication_reports_unknown_and_keeps_committed_proposal(
    tmp_path: Path,
) -> None:
    paths = TlonPaths(home=tmp_path)
    executor = _bound(paths)
    rename = Path.rename

    def committed_then_failed(source: Path, target: Path) -> Path:
        result = rename(source, target)
        if source.name == "package":
            raise OSError("acknowledgment failed")
        return result

    with (
        patch.object(Path, "rename", committed_then_failed),
        pytest.raises(UnknownOutcomeError) as failure,
    ):
        await OperationTool(executor, "prototype-write").execute_context(
            {"behavior": BUNDLE_BEHAVIOR, "modules": BUNDLE_MODULES, "display_name": "x"}
        )
    assert failure.value.data == {
        "outcome": "unknown",
        "call_id": "call-alice",
        "retry_safe": False,
    }
    proposals = list_staging_prototypes(paths)
    assert len(proposals) == 1 and proposals[0].trusted is False
    assert (
        paths.prototypes_staging_dir() / proposals[0].name / "behavior.tlon"
    ).read_text() == BUNDLE_BEHAVIOR + "\n"


def test_concurrent_same_name_publication_never_overwrites(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    target = tmp_path / "staging" / "proposal"

    def write(value: str) -> str:
        try:
            publish_directory(target, {"payload": value})
        except FileExistsError:
            return "rejected"
        return value

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, ("first", "second")))
    assert results.count("rejected") == 1
    assert (target / "payload").read_text() == next(
        value for value in results if value != "rejected"
    )
    assert list(tmp_path.glob(".proposal-*")) == []


async def test_member_explicit_review_uses_shared_contract_without_model(tmp_path: Path) -> None:
    from functools import partial

    from tlon.agents.prototypes.catalog import list_prototypes
    from tlon.agents.prototypes.lineage import get_prototype_lineage
    from tlon.agents.prototypes.reviews import (
        read_prototype,
        recover_prototype_review,
        submit_prototype_review,
    )
    from tlon.evolution.get_lineage import PrototypeLineage
    from tlon.evolution.list_prototypes import PrototypeCatalog

    paths = TlonPaths(home=tmp_path)
    proposal = write_staging_prototype(
        _SPEC, modules=_MODULES, display_name="x", author_id="author", tlon_paths=paths
    )
    operations = make_evolution_operations(
        "reviewer",
        catalog=PrototypeCatalog(partial(list_prototypes, tlon_paths=paths)),
        staging=StagingCatalog(partial(list_staging_prototypes, tlon_paths=paths)),
        lineage=PrototypeLineage(partial(get_prototype_lineage, tlon_paths=paths)),
        prototype_reader=partial(read_prototype, tlon_paths=paths),
        review_submitter=partial(submit_prototype_review, tlon_paths=paths),
        review_recoverer=partial(recover_prototype_review, tlon_paths=paths),
    )
    executor = OperationExecutor(
        OperationRegistry(operations), CallContext("reviewer", "run", "call")
    )
    adapter = DSLAdapter(executor)
    for name, args in (
        ("prototype-list", {}),
        ("prototype-staging-list", {}),
        ("prototype-lineage", {"name": proposal.name}),
        ("prototype-read", {"name": proposal.name}),
    ):
        result = await OperationTool(executor, name).execute_context(args)
        assert result.success, result.error
        actual = await adapter.handle(
            BuiltinCall(
                name,
                adapter.builtins()[name],
                [{Keyword(key): value for key, value in args.items()}],
                {},
            )
        )
        assert json_value(actual, edn_keys=True) == result.data
    material = await executor.call(
        "prototype-read", {"name": proposal.name, "path": "behavior.tlon"}
    )
    assert material["content"] == _SPEC + "\n"
    args = {
        "name": proposal.name,
        "digest": material["digest"],
        "verdict": "通过",
        "reason": "材料完整",
    }
    with patch(
        "tlon.base.llm_client.LLMClient.chat", side_effect=AssertionError("hidden model call")
    ):
        result = await OperationTool(executor, "prototype-review-submit").execute_context(args)
        assert result.success, result.error
        repeated = await adapter.handle(
            BuiltinCall(
                "prototype-review-submit",
                adapter.builtins()["prototype-review-submit"],
                [{Keyword(key): value for key, value in args.items()}],
                {},
            )
        )
    assert json_value(repeated, edn_keys=True) == result.data
    assert result.data["reviewer"] == "reviewer"
    assert result.data["run_id"] == "run" and result.data["call_id"] == "call"
    assert list_staging_prototypes(paths)[0].trusted is False
    with pytest.raises(OperationError, match="unknown operation"):
        executor.registry.get("prototype-review")
    forged = await OperationTool(executor, "prototype-review-submit").execute_context(
        {**args, "reviewer_id": "author"}
    )
    assert not forged.success and "InputError" in forged.error
