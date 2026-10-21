"""Approval freezes transitive identities; validation precedes publication."""

from pathlib import Path

import pytest

from tests.agents.capabilities.test_spec import PURE
from tlon.agents.capabilities.bridge import load_capability_catalog
from tlon.agents.capabilities.catalog import get_capability_detail, list_trusted_capabilities
from tlon.agents.capabilities.spec import CapabilityError
from tlon.agents.capabilities.staging import (
    append_staging_review,
    approve_capability,
    list_staging_capabilities,
    reject_capability,
    write_staging_capability,
)
from tlon.base.paths import TlonPaths
from tlosure.edn import loads


def test_stage_review_approve_and_catalog(tmp_path: Path) -> None:
    paths = TlonPaths(home=tmp_path)
    info = write_staging_capability(
        PURE, author_id="adam", proposal_note="new word", tlon_paths=paths
    )
    assert not info.trusted and info.author == "adam" and info.effects == ()
    append_staging_review("shout", "review 1", paths)
    append_staging_review("shout", "review 2", paths)
    assert [info.name for info in list_staging_capabilities(paths)] == ["shout"]
    info = approve_capability("shout", paths)
    assert info.trusted and info.definition_id and info.error is None
    assert not paths.capabilities_staging_dir().joinpath("shout").exists()
    meta = loads((paths.capabilities_dir() / "shout/meta.tlon").read_text())
    assert meta["definition-id"] == info.definition_id and meta["dependencies"] == {}
    detail, text, reviews = get_capability_detail("shout", tlon_paths=paths)
    assert detail == info and text == PURE + "\n"
    assert "review 1" in reviews and "review 2" in reviews
    assert list_trusted_capabilities(paths) == [info]


@pytest.mark.parametrize(
    "change",
    [
        lambda text: text.replace('(str (get input :text) "!")', "(missing input)"),
        lambda text: text.replace(':name "shout"', ':name "count"'),
        lambda text: text.replace(':expect "hey!"', ':expect "bad"'),
        lambda text: text.replace(":version 2", ":version 1"),
    ],
)
def test_invalid_staging_never_touches_disk(tmp_path: Path, change) -> None:
    paths = TlonPaths(home=tmp_path)
    with pytest.raises(CapabilityError):
        write_staging_capability(change(PURE), tlon_paths=paths)
    assert not paths.capabilities_staging_dir().exists()


def test_duplicate_and_reject_path_guard(tmp_path: Path) -> None:
    paths = TlonPaths(home=tmp_path)
    write_staging_capability(PURE, tlon_paths=paths)
    with pytest.raises(CapabilityError, match="already exists"):
        write_staging_capability(PURE, tlon_paths=paths)
    with pytest.raises(CapabilityError, match="invalid capability name"):
        reject_capability("../shout", paths)
    reject_capability("shout", paths)
    assert list_staging_capabilities(paths) == []


def test_approval_revalidates_edited_staging(tmp_path: Path) -> None:
    paths = TlonPaths(home=tmp_path)
    write_staging_capability(PURE, tlon_paths=paths)
    artifact = paths.capabilities_staging_dir() / "shout/capability.tlon"
    artifact.write_text(PURE.replace('(str (get input :text) "!")', "(missing input)"))
    with pytest.raises(CapabilityError):
        approve_capability("shout", paths)
    assert artifact.exists() and not (paths.capabilities_dir() / "shout").exists()


def test_handwritten_needs_explicit_approval(tmp_path: Path) -> None:
    paths = TlonPaths(home=tmp_path)
    directory = paths.capabilities_dir() / "shout"
    directory.mkdir(parents=True)
    (directory / "capability.tlon").write_text(PURE)
    assert not load_capability_catalog(paths).definitions
    assert approve_capability("shout", paths).trusted
    assert "shout" in load_capability_catalog(paths).definitions


def test_malformed_approval_metadata_rejects_without_publishing(tmp_path: Path) -> None:
    paths = TlonPaths(home=tmp_path)
    write_staging_capability(PURE, tlon_paths=paths)
    metadata = paths.capabilities_staging_dir() / "shout/meta.tlon"
    metadata.write_text("{:trusted")
    with pytest.raises(CapabilityError, match="invalid capability metadata"):
        approve_capability("shout", paths)
    assert metadata.exists() and not (paths.capabilities_dir() / "shout").exists()


@pytest.mark.parametrize("name", ["run-loop", "mail-field", "self", "msg", "event"])
def test_capability_cannot_shadow_behavior_vocabulary(tmp_path: Path, name: str) -> None:
    paths = TlonPaths(home=tmp_path)
    with pytest.raises(CapabilityError, match="collides with reserved vocabulary"):
        write_staging_capability(PURE.replace('"shout"', f'"{name}"'), tlon_paths=paths)
    assert not paths.capabilities_staging_dir().exists()
