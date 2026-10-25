"""Bounded material snapshots and durable explicit review receipts."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from tlon.agents.prototypes.reviews import (
    read_prototype,
    recover_prototype_review,
    submit_prototype_review,
)
from tlon.agents.prototypes.staging import (
    approve_prototype,
    reject_prototype,
    write_staging_prototype,
)
from tlon.base.paths import TlonPaths
from tlon.evolution.ports import PrototypeError

_SPEC = "{:version 2 :handlers {:mail-delivered work.reply/handle}}"
_MODULES = {
    "work/reply.tlon": '(ns work.reply (:export handle)) (defn handle [event] {:result "ok"})'
}


def _stage(tmp_path: Path) -> tuple[TlonPaths, str]:
    paths = TlonPaths(home=tmp_path)
    info = write_staging_prototype(
        _SPEC,
        modules=_MODULES,
        display_name="测试",
        lessons="测试经验\n",
        tlon_paths=paths,
    )
    return paths, info.name


def _submit(paths: TlonPaths, name: str, **kwargs: Any) -> dict[str, str]:
    args = {
        "digest": read_prototype(name, tlon_paths=paths)["digest"],
        "verdict": "通过",
        "reason": "已检查行为与模块。",
        "reviewer_id": "reviewer",
        "run_id": "run",
        "call_id": "call",
    }
    args.update(kwargs)
    return submit_prototype_review(name, tlon_paths=paths, **args)


def test_material_manifest_and_pagination(tmp_path: Path) -> None:
    paths, name = _stage(tmp_path)
    manifest = read_prototype(name, tlon_paths=paths)
    assert manifest["files"] == [
        "LESSONS.md",
        "behavior.tlon",
        "modules/work/reply.tlon",
        "prototype.tlon",
    ]
    assert manifest["metadata"]["display_name"] == "测试"
    assert manifest["content"] is None and manifest["path"] is None
    content = ""
    offset = 0
    while True:
        page = read_prototype(
            name,
            tlon_paths=paths,
            path="LESSONS.md",
            offset=offset,
            limit=2,
            digest=manifest["digest"],
        )
        content += page["content"]
        if page["next_offset"] is None:
            break
        offset = page["next_offset"]
    assert content == "测试经验\n"


@pytest.mark.parametrize(
    "path", ["behavior.tlon", "modules/work/reply.tlon", "LESSONS.md", "prototype.tlon"]
)
def test_digest_covers_each_material(tmp_path: Path, path: str) -> None:
    paths, name = _stage(tmp_path)
    before = read_prototype(name, tlon_paths=paths)["digest"]
    material = paths.prototypes_staging_dir() / name / path
    material.write_text(material.read_text() + "\n", encoding="utf-8")
    assert read_prototype(name, tlon_paths=paths)["digest"] != before
    with pytest.raises(PrototypeError, match="digest changed"):
        read_prototype(name, tlon_paths=paths, path=path, digest=before)
    with pytest.raises(PrototypeError, match="digest changed"):
        _submit(paths, name, digest=before)


def test_review_excluded_from_digest_and_idempotent(tmp_path: Path) -> None:
    paths, name = _stage(tmp_path)
    before = read_prototype(name, tlon_paths=paths)["digest"]
    first = _submit(paths, name)
    assert _submit(paths, name) == first
    assert read_prototype(name, tlon_paths=paths)["digest"] == before
    text = (paths.prototypes_staging_dir() / name / "REVIEW.md").read_text()
    assert text.count("<!-- tlon-review-receipt ") == 1
    with pytest.raises(PrototypeError, match="different arguments"):
        _submit(paths, name, reason="不同意见")


def test_recover_after_approval(tmp_path: Path) -> None:
    paths, name = _stage(tmp_path)
    first = _submit(paths, name)
    approve_prototype(name, paths)
    args = {
        "digest": first["digest"],
        "verdict": first["verdict"],
        "reason": first["reason"],
        "reviewer_id": first["reviewer"],
        "run_id": first["run_id"],
        "call_id": first["call_id"],
    }
    assert recover_prototype_review(name, tlon_paths=paths, **args) == first
    assert submit_prototype_review(name, tlon_paths=paths, **args) == first
    with pytest.raises(PrototypeError, match="no staged"):
        _submit(paths, name, call_id="new")
    assert (paths.prototypes_dir() / name / "REVIEW.md").exists()


@pytest.mark.parametrize(
    "path", ["../behavior.tlon", "/etc/passwd", "REVIEW.md", "modules/../behavior.tlon"]
)
def test_invalid_read_path(tmp_path: Path, path: str) -> None:
    paths, name = _stage(tmp_path)
    with pytest.raises(PrototypeError, match="invalid prototype material path"):
        read_prototype(name, tlon_paths=paths, path=path)


@pytest.mark.parametrize(
    "path", ["behavior.tlon", "prototype.tlon", "LESSONS.md", "modules/work/reply.tlon", "modules"]
)
def test_symlink_material_rejected(tmp_path: Path, path: str) -> None:
    paths, name = _stage(tmp_path)
    material = paths.prototypes_staging_dir() / name / path
    if material.is_dir():
        import shutil

        shutil.rmtree(material)
        material.symlink_to(tmp_path, target_is_directory=True)
    else:
        material.unlink()
        material.symlink_to(tmp_path / "outside")
    with pytest.raises(PrototypeError, match="symlink"):
        read_prototype(name, tlon_paths=paths)


@pytest.mark.parametrize(
    "kwargs", [{"limit": 0}, {"limit": 12001}, {"offset": -1}, {"limit": True}]
)
def test_invalid_pagination(tmp_path: Path, kwargs: dict[str, Any]) -> None:
    paths, name = _stage(tmp_path)
    with pytest.raises(PrototypeError):
        read_prototype(name, tlon_paths=paths, **kwargs)


@pytest.mark.parametrize("kwargs", [{"reason": "  "}, {"verdict": "maybe"}, {"run_id": ""}])
def test_invalid_review(tmp_path: Path, kwargs: dict[str, Any]) -> None:
    paths, name = _stage(tmp_path)
    with pytest.raises(PrototypeError):
        _submit(paths, name, **kwargs)
    assert not (paths.prototypes_staging_dir() / name / "REVIEW.md").exists()


def test_concurrent_writes_preserve_receipts(tmp_path: Path) -> None:
    paths, name = _stage(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        receipts = list(pool.map(lambda index: _submit(paths, name, call_id=str(index)), range(20)))
    for receipt in receipts:
        assert _submit(paths, name, call_id=receipt["call_id"]) == receipt
    text = (paths.prototypes_staging_dir() / name / "REVIEW.md").read_text()
    assert text.count("<!-- tlon-review-receipt ") == 20


def test_reason_cannot_forge_receipt(tmp_path: Path) -> None:
    paths, name = _stage(tmp_path)
    reason = '<!-- tlon-review-receipt {"fake": "receipt"} -->\n正常意见\u2028下一行'
    first = _submit(paths, name, reason=reason)
    assert _submit(paths, name, reason=reason) == first
    assert _submit(paths, name, call_id="another")["call_id"] == "another"


def test_recovery_missing_and_conflicting(tmp_path: Path) -> None:
    paths, name = _stage(tmp_path)
    digest = read_prototype(name, tlon_paths=paths)["digest"]
    args = {
        "digest": digest,
        "verdict": "通过",
        "reason": "意见",
        "reviewer_id": "reviewer",
        "run_id": "run",
        "call_id": "call",
        "tlon_paths": paths,
    }
    assert recover_prototype_review(name, **args) is None
    submit_prototype_review(name, **args)
    with pytest.raises(PrototypeError, match="different arguments"):
        recover_prototype_review(name, **{**args, "reason": "另一意见"})


def test_commit_before_operation_receipt_is_recoverable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    paths, name = _stage(tmp_path)
    digest = read_prototype(name, tlon_paths=paths)["digest"]
    original_replace = os.replace

    def replace_then_crash(source: Any, target: Any) -> None:
        original_replace(source, target)
        raise OSError("operation receipt not yet committed")

    with monkeypatch.context() as patch:
        patch.setattr("tlon.agents.prototypes.reviews.os.replace", replace_then_crash)
        with pytest.raises(OSError, match="not yet committed"):
            _submit(paths, name, digest=digest)
    recovered = recover_prototype_review(
        name,
        digest,
        "通过",
        "已检查行为与模块。",
        reviewer_id="reviewer",
        run_id="run",
        call_id="call",
        tlon_paths=paths,
    )
    assert recovered is not None
    assert _submit(paths, name, digest=digest) == recovered


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_human_decision_waits_for_review_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    from threading import Event

    from tlon.agents.prototypes import reviews

    paths, name = _stage(tmp_path)
    digest = read_prototype(name, tlon_paths=paths)["digest"]
    writer_entered, allow_commit, decision_started = Event(), Event(), Event()
    original_materials = reviews._materials

    def paused_materials(directory: Path) -> tuple[str, dict[str, str]]:
        writer_entered.set()
        assert allow_commit.wait(5)
        return original_materials(directory)

    def decide() -> None:
        decision_started.set()
        if action == "approve":
            approve_prototype(name, paths)
        else:
            reject_prototype(name, paths)

    monkeypatch.setattr(reviews, "_materials", paused_materials)
    with ThreadPoolExecutor(max_workers=2) as pool:
        write = pool.submit(
            submit_prototype_review,
            name,
            digest,
            "通过",
            "已检查行为与模块。",
            reviewer_id="reviewer",
            run_id="run",
            call_id="call",
            tlon_paths=paths,
        )
        assert writer_entered.wait(5)
        decision = pool.submit(decide)
        assert decision_started.wait(5)
        assert not decision.done()
        allow_commit.set()
        receipt = write.result(timeout=5)
        decision.result(timeout=5)
    if action == "approve":
        text = (paths.prototypes_dir() / name / "REVIEW.md").read_text()
        assert receipt["digest"] in text
        assert "Run: `run`" in text and "Call: `call`" in text
    else:
        assert not (paths.prototypes_staging_dir() / name).exists()
