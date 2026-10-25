"""Explicit review writes recover their own committed receipt without model calls."""

from functools import partial
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from tlon.agents.program_store import ProgramStore
from tlon.agents.prototypes.reviews import (
    read_prototype,
    recover_prototype_review,
    submit_prototype_review,
)
from tlon.agents.prototypes.staging import approve_prototype, write_staging_prototype
from tlon.base.execution import ExecutionDeadlineError, ExecutionScope, bind_execution
from tlon.base.paths import TlonPaths
from tlon.operations.core import CallContext, OperationExecutor, OperationRegistry
from tlon.operations.evolution import make_evolution_operations
from tlon.operations.invocation import InvocationOutcome
from tlon.operations.schema import RejectedError, UnknownOutcomeError

_SPEC = "{:version 2 :handlers {:boot work/handle}}"
_MODULES = {"work.tlon": "(ns work (:export handle)) (defn handle [event] {})"}


def _registry(paths: TlonPaths) -> OperationRegistry:
    return OperationRegistry(
        make_evolution_operations(
            "alice",
            False,
            prototype_reader=partial(read_prototype, tlon_paths=paths),
            review_submitter=partial(submit_prototype_review, tlon_paths=paths),
            review_recoverer=partial(recover_prototype_review, tlon_paths=paths),
        )
    )


def _proposal(paths: TlonPaths) -> dict[str, str]:
    info = write_staging_prototype(
        _SPEC, modules=_MODULES, display_name="candidate", tlon_paths=paths
    )
    return {
        "name": info.name,
        "digest": read_prototype(info.name, tlon_paths=paths)["digest"],
        "verdict": "通过",
        "reason": "入口与模块齐全",
    }


@pytest.mark.parametrize("approved", [False, True])
async def test_review_recovers_committed_write_after_receipt_failure(
    tmp_path: Path, approved: bool
) -> None:
    paths = TlonPaths(home=tmp_path)
    args = _proposal(paths)
    store = ProgramStore(tmp_path / "calls.sqlite", build_id="test")
    context = CallContext("alice", "root", "review-call")
    first = OperationExecutor(_registry(paths), context, store)
    with (
        bind_execution(ExecutionScope(store, "root", 100, "output")),
        patch.object(store, "commit_invocation", side_effect=OSError("receipt disk unavailable")),
        pytest.raises(UnknownOutcomeError),
    ):
        await first.call("prototype-review-submit", args)
    review_file = paths.prototypes_staging_dir() / args["name"] / "REVIEW.md"
    before = review_file.read_text()
    assert before.count("<!-- tlon-review-receipt ") == 1
    if approved:
        approve_prototype(args["name"], paths)
        review_file = paths.prototypes_dir() / args["name"] / "REVIEW.md"
    # A fresh executor must reconcile the domain receipt without submitting again.
    operations = make_evolution_operations(
        "alice",
        False,
        review_submitter=Mock(side_effect=AssertionError("must recover, not resubmit")),
        review_recoverer=partial(recover_prototype_review, tlon_paths=paths),
    )
    fresh = OperationExecutor(OperationRegistry(operations), context, store)
    with bind_execution(ExecutionScope(store, "root", 100, "output")):
        result = await fresh.call("prototype-review-submit", args)
        replay = await fresh.call("prototype-review-submit", args)
    assert result == replay
    assert result["reviewer"] == "alice" and result["call_id"] == "review-call"
    assert review_file.read_text() == before


async def test_stale_material_and_trusted_submission_are_known_rejections(tmp_path: Path) -> None:
    paths = TlonPaths(home=tmp_path)
    args = _proposal(paths)
    executor = OperationExecutor(_registry(paths), CallContext("alice", "root", "call"))
    material = paths.prototypes_staging_dir() / args["name"] / "LESSONS.md"
    material.write_text("new lessons")
    with pytest.raises(RejectedError, match="digest changed"):
        await executor.call("prototype-review-submit", args)
    args["digest"] = read_prototype(args["name"], tlon_paths=paths)["digest"]
    approve_prototype(args["name"], paths)
    with pytest.raises(RejectedError, match="staged"):
        await executor.call("prototype-review-submit", args)
    assert not (paths.prototypes_dir() / args["name"] / "REVIEW.md").exists()


async def test_review_write_io_failure_is_unknown_and_does_not_retry(tmp_path: Path) -> None:
    paths = TlonPaths(home=tmp_path)
    args = _proposal(paths)
    executor = OperationExecutor(_registry(paths), CallContext("alice", "root", "call"))
    with (
        patch("tlon.agents.prototypes.reviews.os.replace", side_effect=OSError("disk failure")),
        pytest.raises(UnknownOutcomeError) as failure,
    ):
        await executor.call("prototype-review-submit", args)
    assert failure.value.data == {"outcome": "unknown", "call_id": "call", "retry_safe": False}
    assert not (paths.prototypes_staging_dir() / args["name"] / "REVIEW.md").exists()


async def test_evolution_wrapper_preserves_known_deadline() -> None:
    reader = Mock(side_effect=ExecutionDeadlineError("known deadline"))
    registry = OperationRegistry(make_evolution_operations("alice", False, prototype_reader=reader))
    executor = OperationExecutor(registry, CallContext("alice", "root", "call"))
    with pytest.raises(ExecutionDeadlineError, match="known deadline"):
        await executor.call("prototype-read", {"name": "proposal"})


@pytest.mark.parametrize("fault", ["corrupt-receipt", "read-unavailable"])
@pytest.mark.parametrize("receipt_state", ["pending", "unknown"])
async def test_review_recovery_cannot_turn_unverifiable_receipt_into_rejection(
    tmp_path: Path, fault: str, receipt_state: str
) -> None:
    paths = TlonPaths(home=tmp_path)
    args = _proposal(paths)
    store = ProgramStore(tmp_path / "calls.sqlite", build_id="test")
    context = CallContext("alice", "root", "review-call")
    first = OperationExecutor(_registry(paths), context, store)
    with (
        bind_execution(ExecutionScope(store, "root", 100, "output")),
        patch.object(store, "commit_invocation", side_effect=OSError("receipt unavailable")),
        pytest.raises(UnknownOutcomeError),
    ):
        await first.call("prototype-review-submit", args)
    review_file = paths.prototypes_staging_dir() / args["name"] / "REVIEW.md"
    text = review_file.read_text()
    assert text.count("<!-- tlon-review-receipt ") == 1
    if fault == "corrupt-receipt":
        review_file.write_text(
            text.replace("<!-- tlon-review-receipt {", "<!-- tlon-review-receipt INVALID{")
        )
        recoverer = partial(recover_prototype_review, tlon_paths=paths)
    else:
        recoverer = Mock(side_effect=OSError("receipt read unavailable"))
    submitter = Mock(side_effect=AssertionError("must not resubmit"))
    registry = OperationRegistry(
        make_evolution_operations(
            "alice",
            False,
            review_submitter=submitter,
            review_recoverer=recoverer,
        )
    )
    fresh = OperationExecutor(registry, context, store)
    invocation = first._invocation(first.registry.get("prototype-review-submit"), args, context)
    assert store.lookup_invocation(invocation) is None
    if receipt_state == "unknown":
        store.commit_invocation(
            invocation,
            InvocationOutcome(
                False,
                error_type="UnknownOutcomeError",
                error_message="receipt commit failed",
                error_data={"outcome": "unknown", "call_id": "review-call", "retry_safe": False},
            ),
        )
    before = store.lookup_invocation(invocation)
    with bind_execution(ExecutionScope(store, "root", 100, "output")):
        for _ in range(2):
            with pytest.raises(UnknownOutcomeError) as failure:
                await fresh.call("prototype-review-submit", args)
            assert failure.value.data == {
                "call_id": "review-call",
                "outcome": "unknown",
                "retry_safe": False,
            }
    assert store.lookup_invocation(invocation) == before
    submitter.assert_not_called()
    assert review_file.read_text().count("<!-- tlon-review-receipt ") == 1
