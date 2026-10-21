"""Human publication binds and copies the entire executable program package."""

from pathlib import Path

import pytest

from tlon.agents.prototypes.instantiate import instantiate
from tlon.agents.prototypes.management import update_prototype, write_trusted_prototype
from tlon.agents.prototypes.staging import approve_prototype, write_staging_prototype
from tlon.api.prototypes import PrototypesFacade
from tlon.base.paths import TlonPaths
from tlon.evolution.ports import PrototypeError

BEHAVIOR = "{:version 2 :handlers {:mail-delivered work.reply/handle}}\n"
MODULES = {"work/reply.tlon": "(ns work.reply (:export handle)) (defn handle [event] event)"}


def test_publish_copy_and_reject_tampered_module(tmp_path: Path) -> None:
    paths = TlonPaths(home=tmp_path)
    info = write_trusted_prototype(
        BEHAVIOR, display_name="Program", modules=MODULES, tlon_paths=paths
    )
    instantiate(info.name, "first", "First", model="deepseek/test", tlon_paths=paths)
    copied = paths.agent_config_dir("first") / "modules/work/reply.tlon"
    assert copied.read_text() == MODULES["work/reply.tlon"]
    source = paths.prototypes_dir() / info.name / "modules/work/reply.tlon"
    source.write_text(MODULES["work/reply.tlon"] + "\n")
    update_prototype(info.name, description="metadata must not reapprove code", tlon_paths=paths)
    with pytest.raises(PrototypeError, match="approved package"):
        instantiate(info.name, "second", "Second", model="deepseek/test", tlon_paths=paths)
    assert not paths.agent_config_dir("second").exists()


def test_approval_checks_current_complete_package(tmp_path: Path) -> None:
    paths = TlonPaths(home=tmp_path)
    info = write_staging_prototype(
        BEHAVIOR, display_name="Program", modules=MODULES, tlon_paths=paths
    )
    source = paths.prototypes_staging_dir() / info.name / "modules/work/reply.tlon"
    source.write_text("(ns work.reply (:export missing))")
    with pytest.raises(PrototypeError):
        approve_prototype(info.name, paths)
    assert source.exists()
    source.write_text(MODULES["work/reply.tlon"] + "\n")
    evidence = source.parents[2] / "arena/run-1/result.bin"
    evidence.parent.mkdir(parents=True)
    evidence.write_bytes(b"\x00\xff")
    approve_prototype(info.name, paths)
    assert (
        paths.prototypes_dir() / info.name / "arena/run-1/result.bin"
    ).read_bytes() == b"\x00\xff"
    instantiate(info.name, "first", "First", model="deepseek/test", tlon_paths=paths)
    assert PrototypesFacade(tlon_paths=paths).get_modules(info.name) == {
        "work/reply.tlon": MODULES["work/reply.tlon"] + "\n"
    }


def test_invalid_update_leaves_entire_package_untouched(tmp_path: Path) -> None:
    paths = TlonPaths(home=tmp_path)
    info = write_trusted_prototype(
        BEHAVIOR, display_name="Program", modules=MODULES, tlon_paths=paths
    )
    root = paths.prototypes_dir() / info.name
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    with pytest.raises(PrototypeError):
        update_prototype(info.name, modules={"../escape.tlon": "(ns escape)"}, tlon_paths=paths)
    assert before == {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    with pytest.raises(PrototypeError, match="immutable"):
        update_prototype(info.name, modules=MODULES, tlon_paths=paths)
    with pytest.raises(PrototypeError, match="immutable"):
        update_prototype(info.name, behavior_edn=BEHAVIOR, tlon_paths=paths)
    update_prototype(info.name, description="New description", tlon_paths=paths)
    instantiate(info.name, "first", "First", model="deepseek/test", tlon_paths=paths)


def test_symlink_modules_rejected_before_copy(tmp_path: Path) -> None:
    paths = TlonPaths(home=tmp_path)
    info = write_trusted_prototype(
        BEHAVIOR, display_name="Program", modules=MODULES, tlon_paths=paths
    )
    root = paths.prototypes_dir() / info.name
    (root / "modules/other.tlon").symlink_to(root / "modules/work/reply.tlon")
    with pytest.raises(PrototypeError, match="symlink"):
        instantiate(info.name, "first", "First", model="deepseek/test", tlon_paths=paths)


def test_v1_rejects_module_payload(tmp_path: Path) -> None:
    with pytest.raises(PrototypeError, match="version 1"):
        write_trusted_prototype(
            '{:version 1 :rules [{:name "noop" :on :mail-delivered :actions [(+ 1 2)]}]}',
            display_name="Old",
            modules=MODULES,
            tlon_paths=TlonPaths(home=tmp_path),
        )
