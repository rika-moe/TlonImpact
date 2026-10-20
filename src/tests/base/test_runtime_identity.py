"""Recovery fingerprints follow runtime semantics rather than checkout location."""

from pathlib import Path

from tlon.base.runtime_identity import build_identity, runtime_identity


def test_identity_freezes_code_lock_and_dependency_versions(tmp_path: Path) -> None:
    code = tmp_path / "runtime"
    code.mkdir()
    source = code / "operation.py"
    source.write_text("value = 1\n")
    first = build_identity([code], "lock-a", [("dep", "1")])
    assert build_identity([code], "lock-b", [("dep", "1")]) != first
    assert build_identity([code], "lock-a", [("dep", "2")]) != first
    source.write_text("value = 2\n")
    assert build_identity([code], "lock-a", [("dep", "1")]) != first


def test_identity_is_path_order_independent_and_excludes_documents(tmp_path: Path) -> None:
    first_root = tmp_path / "first" / "runtime"
    second_root = tmp_path / "second" / "runtime"
    for root in (first_root, second_root):
        root.mkdir(parents=True)
        (root / "main.py").write_text("value = 1\n")
    first = build_identity([first_root], "lock", [("b", "2"), ("a", "1")])
    (second_root / "README.md").write_text("docs may change independently")
    assert build_identity([second_root], "lock", [("a", "1"), ("b", "2")]) == first


def test_process_runtime_identity_is_stable() -> None:
    assert len(runtime_identity()) == 64
    assert runtime_identity() == runtime_identity()
