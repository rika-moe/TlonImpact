"""Ordinary Lisp reviews use explicit reads and durable advisory submissions."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from tlon.agents.program_driver import ProgramDriver
from tlon.agents.program_package import program_builtins, program_module_sources
from tlon.agents.program_store import ProgramStore
from tlon.agents.prototypes.reviews import (
    read_prototype,
    recover_prototype_review,
    submit_prototype_review,
)
from tlon.agents.prototypes.staging import approve_prototype, write_staging_prototype
from tlon.agents.vocabulary import make_member_builtins
from tlon.base.paths import TlonPaths
from tlon.operations.core import CallContext, OperationExecutor, OperationRegistry
from tlon.operations.dsl import DSLAdapter
from tlon.operations.evolution import make_evolution_operations
from tlon.operations.invocation import Invocation, InvocationOutcome, registry_identity
from tlon.operations.model import make_model_operations
from tlon.operations.schema import json_value
from tlosure.modules import ModulePackage

RUN_ID = "mail:review-request:handler:mail-delivered"
REVIEWER = "reviewer"
BEHAVIOR = "{:version 2 :handlers {:mail-delivered proposal.main/handle}}\n"
PROPOSAL_MODULES = {
    "proposal/main.tlon": "(ns proposal.main (:export handle)) "
    "(defn handle [event] {:result nil})\n"
}
WORKFLOW = """(ns review.main (:export handle))
(defn handle [event]
  (let [name (get event :name)
        proposal (prototype-read {:name name})
        version (get proposal :digest)
        behavior (prototype-read {:name name :digest version :path "behavior.tlon"})
        source (prototype-read {:name name :digest version :path "modules/proposal/main.tlon"})
        lessons (prototype-read {:name name :digest version :path "LESSONS.md"})]
    (prototype-review-submit
      {:name name :digest version :verdict "通过"
       :reason (str "已检查入口、模块与教训：" (get lessons :content))})))
"""


class PowerLoss(BaseException):
    """Interrupt after domain commit and before the operation receipt is saved."""


class ReviewHarness:
    def __init__(self, home: Path) -> None:
        self.paths = TlonPaths(home=home)
        self.proposal = write_staging_prototype(
            BEHAVIOR,
            display_name="显式评审候选",
            author_id="author",
            lessons="没有承诺自动晋级。",
            modules=PROPOSAL_MODULES,
            tlon_paths=self.paths,
        )
        self.database = home / "reviewer/program.sqlite3"
        self.llm = SimpleNamespace(chat=AsyncMock())
        self.reads = 0
        self.submissions = 0
        self.recoveries = 0
        self.restart()

    def restart(self) -> None:
        self.store = ProgramStore(self.database, build_id=registry_identity(make_member_builtins()))

    def read(self, **arguments: Any) -> dict[str, Any]:
        self.reads += 1
        return read_prototype(**arguments, tlon_paths=self.paths)

    def submit(self, **arguments: Any) -> dict[str, str]:
        self.submissions += 1
        return submit_prototype_review(**arguments, tlon_paths=self.paths)

    def recover(self, **arguments: Any) -> dict[str, str] | None:
        self.recoveries += 1
        return recover_prototype_review(**arguments, tlon_paths=self.paths)

    async def execute(self) -> dict[str, Any]:
        operations = DSLAdapter(
            OperationExecutor(
                OperationRegistry(
                    (
                        *make_evolution_operations(
                            REVIEWER,
                            prototype_reader=self.read,
                            review_submitter=self.submit,
                            review_recoverer=self.recover,
                        ),
                        *make_model_operations(self.llm, AsyncMock(return_value="评审成员")),
                    )
                ),
                CallContext(REVIEWER, RUN_ID, RUN_ID),
                store=self.store,
            )
        )
        builtins = {**program_builtins(), **operations.builtins()}
        package = ModulePackage.from_sources(
            program_module_sources({"review/main.tlon": WORKFLOW}), builtins
        )
        result = await ProgramDriver(
            self.store, RUN_ID, builtins, operations=operations, member_id=REVIEWER
        ).run_entry(package.entry("review.main/handle"), {"name": self.proposal.name}, fuel=10000)
        return json_value(result, edn_keys=True)

    @property
    def review_file(self) -> Path:
        return self.paths.prototypes_staging_dir() / self.proposal.name / "REVIEW.md"


def assert_host_receipt(receipt: dict[str, Any], harness: ReviewHarness) -> None:
    assert receipt["name"] == harness.proposal.name
    assert receipt["reviewer"] == REVIEWER
    assert receipt["run_id"] == RUN_ID
    assert receipt["call_id"] == f"program:{RUN_ID}:4:prototype-review-submit"
    assert receipt["verdict"] == "通过"
    assert receipt["reason"] == "已检查入口、模块与教训：没有承诺自动晋级。"
    assert receipt["created_at"]
    assert len(receipt["digest"]) == 64
    assert "model" not in receipt


async def test_lisp_review_replays_reads_and_submission_without_model_or_approval(
    tmp_path: Path,
) -> None:
    review = ReviewHarness(tmp_path)
    receipt = await review.execute()
    assert_host_receipt(receipt, review)
    original_text = review.review_file.read_text()
    assert original_text.count("<!-- tlon-review-receipt ") == 1
    assert review.reads == 4
    assert review.submissions == 1
    review.llm.chat.assert_not_awaited()
    assert not (review.paths.prototypes_dir() / review.proposal.name).exists()
    assert (
        read_prototype(review.proposal.name, tlon_paths=review.paths)["metadata"]["trusted"]
        is False
    )

    # Later material changes cannot replace already committed read/results in this run.
    lessons = review.review_file.parent / "LESSONS.md"
    lessons.write_text("下一版材料。")
    review.restart()
    assert await review.execute() == receipt
    assert review.reads == 4
    assert review.submissions == 1
    assert review.review_file.read_text() == original_text
    review.llm.chat.assert_not_awaited()


async def test_lisp_recovers_review_after_domain_commit_and_human_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    review = ReviewHarness(tmp_path)
    commit = review.store.commit_invocation

    def interrupted_commit(invocation: Invocation, outcome: InvocationOutcome) -> None:
        if invocation.name == "prototype-review-submit":
            raise PowerLoss()
        commit(invocation, outcome)

    monkeypatch.setattr(review.store, "commit_invocation", interrupted_commit)
    with pytest.raises(PowerLoss):
        await review.execute()
    assert review.submissions == 1
    original_text = review.review_file.read_text()
    assert original_text.count("<!-- tlon-review-receipt ") == 1

    # Approval is an independent human action. Recovery must still find the original
    # record after the proposal moved and approval changed its metadata/digest.
    approve_prototype(review.proposal.name, tlon_paths=review.paths)
    trusted_review = review.paths.prototypes_dir() / review.proposal.name / "REVIEW.md"
    review.restart()
    receipt = await review.execute()
    assert_host_receipt(receipt, review)
    assert review.reads == 4
    assert review.submissions == 1
    assert review.recoveries > 0
    assert trusted_review.read_text() == original_text
    assert not review.review_file.exists()
    assert await review.execute() == receipt
    assert trusted_review.read_text().count("<!-- tlon-review-receipt ") == 1
    review.llm.chat.assert_not_awaited()
