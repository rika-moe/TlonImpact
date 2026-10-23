"""Module-only admission keeps provenance separate from frozen execution."""

import hashlib
from pathlib import Path

import pytest

from tlon.agents.loader import has_frozen_work, load_member_spec
from tlon.agents.member_spec import MemberSpecValidationError
from tlon.base.paths import TlonPaths
from tlosure.edn import EDNParseError

BEHAVIOR = "{:version 2 :handlers {:mail-delivered work.main/handle}}\n"
MODULE = '(ns work.main (:export handle)) (defn handle [event] {:result "ok"})'


def _write(paths: TlonPaths, text: str = BEHAVIOR, source: str = MODULE) -> None:
    behavior = paths.agent_behavior_file("a")
    behavior.parent.mkdir(parents=True, exist_ok=True)
    behavior.write_text(text)
    module = behavior.parent / "modules/work/main.tlon"
    module.parent.mkdir(parents=True, exist_ok=True)
    module.write_text(source)


def test_loads_module_entry_with_raw_source_provenance(tmp_path: Path) -> None:
    paths = TlonPaths(tmp_path)
    _write(paths)
    spec = load_member_spec("a", tlon_paths=paths)
    assert spec.handlers == (("mail-delivered", "work.main/handle"),)
    assert spec.module_sources == {"work/main.tlon": MODULE}
    assert spec.source_hash == hashlib.sha256(BEHAVIOR.encode()).hexdigest()[:12]
    assert not has_frozen_work("a", paths)
    assert not list(tmp_path.rglob("*.sqlite3"))


def test_missing_behavior_refuses_admission(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_member_spec("a", tlon_paths=TlonPaths(tmp_path))


def test_invalid_edn_refuses_admission(tmp_path: Path) -> None:
    paths = TlonPaths(tmp_path)
    _write(paths, "{:version 2 :handlers {")
    with pytest.raises(EDNParseError):
        load_member_spec("a", tlon_paths=paths)


def test_unavailable_module_dependency_refuses_admission(tmp_path: Path) -> None:
    paths = TlonPaths(tmp_path)
    _write(paths, source="(ns work.main (:export handle)) (defn handle [e] (jump e))")
    with pytest.raises(MemberSpecValidationError, match="jump"):
        load_member_spec("a", tlon_paths=paths)


def test_removed_rules_format_is_rejected_without_rewriting_source(tmp_path: Path) -> None:
    paths = TlonPaths(tmp_path)
    text = '{:version 1 :rules [{:name "old" :actions [(run-loop {})]}]}'
    _write(paths, text)
    with pytest.raises(MemberSpecValidationError, match="version"):
        load_member_spec("a", tlon_paths=paths)
    assert paths.agent_behavior_file("a").read_text() == text
