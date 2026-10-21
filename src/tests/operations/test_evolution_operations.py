"""Evolution authority, shared projections and publication failure boundaries."""

from pathlib import Path
from unittest.mock import Mock, patch

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
from tlon.operations.schema import OperationError, RejectedError, json_value
from tlosure.builtins import BuiltinCall
from tlosure.edn import Keyword

_SPEC = '{:version 1 :rules [{:name "boot" :on :boot :actions []}]}'


def _bound(paths: TlonPaths, *, full: bool = True) -> OperationExecutor:
    return OperationExecutor(
        OperationRegistry(
            make_evolution_operations(
                "alice",
                full,
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
    args = {Keyword("behavior"): _SPEC, Keyword("display_name"): "空启动"}
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


@pytest.mark.parametrize(
    ("name", "slot", "args"),
    [
        ("prototype-write", "writer", {"behavior": _SPEC, "display_name": "x"}),
        ("behavior-propose", "behavior_proposer", {"intent": "x", "display_name": "x"}),
    ],
)
async def test_apprentice_denied_in_tool_and_dsl_before_service_call(
    name: str, slot: str, args: dict[str, str]
) -> None:
    service = Mock()
    executor = OperationExecutor(
        OperationRegistry(make_evolution_operations("apprentice", False, **{slot: service})),
        CallContext("apprentice", "run", "call"),
    )
    result = await OperationTool(executor, name).execute_context(args)
    assert not result.success and "RejectedError" in result.error
    adapter = DSLAdapter(executor)
    with pytest.raises(RejectedError):
        await adapter.handle(
            BuiltinCall(
                name,
                adapter.builtins()[name],
                [{Keyword(k): v for k, v in args.items()}],
                {},
            )
        )
    service.execute.assert_not_called()


def test_staging_fsync_failure_leaves_no_partial_package(tmp_path: Path) -> None:
    paths = TlonPaths(home=tmp_path)
    with (
        patch("tlon.evolution.publication.os.fsync", side_effect=OSError("disk failure")),
        pytest.raises(OSError, match="disk failure"),
    ):
        write_staging_prototype(_SPEC, display_name="x", tlon_paths=paths)
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
    with patch("tlon.evolution.publication.os.fsync", side_effect=OSError("disk failure")):
        result = await OperationTool(executor, "prototype-write").execute_context(
            {"behavior": _SPEC, "display_name": "x"}
        )
    assert not result.success
    assert result.data == {"outcome": "unknown", "call_id": "call-alice", "retry_safe": False}


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

    with patch.object(Path, "rename", committed_then_failed):
        result = await OperationTool(executor, "prototype-write").execute_context(
            {"behavior": _SPEC, "display_name": "x"}
        )
    assert not result.success
    assert result.data == {"outcome": "unknown", "call_id": "call-alice", "retry_safe": False}
    proposals = list_staging_prototypes(paths)
    assert len(proposals) == 1 and proposals[0].trusted is False
    assert (
        paths.prototypes_staging_dir() / proposals[0].name / "behavior.tlon"
    ).read_text() == _SPEC + "\n"


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


async def test_probation_can_read_catalogs_lineage_and_review(tmp_path: Path) -> None:
    from functools import partial
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from tlon.agents.prototypes.catalog import get_prototype_detail, list_prototypes
    from tlon.agents.prototypes.lineage import get_prototype_lineage
    from tlon.agents.prototypes.staging import append_staging_review
    from tlon.base.llm_client import LLMResponse
    from tlon.evolution.get_lineage import PrototypeLineage
    from tlon.evolution.list_prototypes import PrototypeCatalog
    from tlon.evolution.review_prototype import PrototypeReviewer

    paths = TlonPaths(home=tmp_path)
    proposal = write_staging_prototype(
        _SPEC, display_name="x", author_id="author", tlon_paths=paths
    )
    llm = SimpleNamespace(
        config=SimpleNamespace(model_name="test/reviewer"),
        chat=AsyncMock(return_value=LLMResponse(content="通过\n材料完整")),
    )
    operations = make_evolution_operations(
        "probation",
        False,
        catalog=PrototypeCatalog(partial(list_prototypes, tlon_paths=paths)),
        staging=StagingCatalog(partial(list_staging_prototypes, tlon_paths=paths)),
        lineage=PrototypeLineage(partial(get_prototype_lineage, tlon_paths=paths)),
        reviewer=PrototypeReviewer(
            "probation",
            llm,
            partial(get_prototype_detail, staging=True, tlon_paths=paths),
            partial(get_prototype_lineage, tlon_paths=paths),
            partial(append_staging_review, tlon_paths=paths),
        ),
    )
    executor = OperationExecutor(
        OperationRegistry(operations), CallContext("probation", "run", "call")
    )
    adapter = DSLAdapter(executor)
    for name, args in (
        ("prototype-list", {}),
        ("prototype-staging-list", {}),
        ("prototype-lineage", {"name": proposal.name}),
        ("prototype-review", {"name": proposal.name}),
    ):
        result = await OperationTool(executor, name).execute_context(args)
        assert result.success, result.error
        await adapter.handle(
            BuiltinCall(
                name,
                adapter.builtins()[name],
                [{Keyword(key): value for key, value in args.items()}],
                {},
            )
        )
    reviews = (paths.prototypes_staging_dir() / proposal.name / "REVIEW.md").read_text()
    assert "probation（test/reviewer）：通过" in reviews
    assert llm.chat.await_count == 2
    assert list_staging_prototypes(paths)[0].trusted is False
