"""Every Behavior publication gate admits approved composite operations."""

from pathlib import Path
from unittest.mock import patch

import pytest

from tests.agents.capabilities.test_spec import PURE
from tlon.agents.capabilities.staging import approve_capability, write_staging_capability
from tlon.agents.prototypes.instantiate import instantiate
from tlon.agents.prototypes.management import update_prototype, write_trusted_prototype
from tlon.agents.prototypes.staging import approve_prototype, write_staging_prototype
from tlon.base.paths import TlonPaths
from tlon.evolution.ports import PrototypeError
from tlon.operations.core import OperationExecutor

BEHAVIOR = """{:version 1 :rules [{:name "use-composite" :on :boot
 :actions [(shout {:text "hello"})]}]}"""
TEMPLATE = """{:version 1 :parameters {:message {:type :string}}
 :rules [{:name "use-composite" :on :boot :actions [(shout {:text ${message}})]}]}"""


def approved(paths: TlonPaths) -> None:
    write_staging_capability(PURE, tlon_paths=paths)
    approve_capability("shout", paths)


@pytest.mark.parametrize("staging", [False, True])
def test_behavior_publication_accepts_approved_composite_without_execution(
    tmp_path: Path, staging: bool
) -> None:
    paths = TlonPaths(home=tmp_path)
    approved(paths)
    writer = write_staging_prototype if staging else write_trusted_prototype
    with patch.object(OperationExecutor, "call_pure", side_effect=AssertionError("no execution")):
        info = writer(BEHAVIOR, display_name="compose", tlon_paths=paths)
    root = paths.prototypes_staging_dir() if staging else paths.prototypes_dir()
    assert (root / info.name / "behavior.tlon").read_text() == BEHAVIOR + "\n"


@pytest.mark.parametrize("staging", [False, True])
def test_behavior_update_uses_same_approved_vocabulary(tmp_path: Path, staging: bool) -> None:
    paths = TlonPaths(home=tmp_path)
    approved(paths)
    writer = write_staging_prototype if staging else write_trusted_prototype
    info = writer(
        '{:version 1 :rules [{:name "noop" :on :boot :actions [(finish-turn {})]}]}',
        display_name="empty",
        tlon_paths=paths,
    )
    update_prototype(info.name, behavior_edn=BEHAVIOR, staging=staging, tlon_paths=paths)
    root = paths.prototypes_staging_dir() if staging else paths.prototypes_dir()
    assert "(shout " in (root / info.name / "behavior.tlon").read_text()


@pytest.mark.parametrize("state", ["missing", "unapproved", "stale"])
def test_unavailable_composite_rejects_behavior_before_publication(
    tmp_path: Path, state: str
) -> None:
    paths = TlonPaths(home=tmp_path)
    if state != "missing":
        write_staging_capability(PURE, tlon_paths=paths)
    if state == "stale":
        approve_capability("shout", paths)
        (paths.capabilities_dir() / "shout/capability.tlon").write_text(PURE.replace('"!"', '"?"'))
    with pytest.raises(PrototypeError, match="unknown function.*shout"):
        write_staging_prototype(BEHAVIOR, display_name="invalid", tlon_paths=paths)
    assert not paths.prototypes_staging_dir().exists()


def test_parameterized_composite_behavior_validates_and_instantiates_without_execution(
    tmp_path: Path,
) -> None:
    paths = TlonPaths(home=tmp_path)
    approved(paths)
    with patch.object(OperationExecutor, "call_pure", side_effect=AssertionError("no execution")):
        info = write_staging_prototype(TEMPLATE, display_name="template", tlon_paths=paths)
        approve_prototype(info.name, paths)
        config = instantiate(
            info.name,
            "worker",
            "Worker",
            model="mock/model",
            params={"message": "hello"},
            tlon_paths=paths,
        )
    assert config.id == "worker"
    text = paths.agent_behavior_file("worker").read_text()
    assert "${message}" not in text and '"hello"' in text


def test_stale_dependency_rejects_instantiation_before_member_files(tmp_path: Path) -> None:
    paths = TlonPaths(home=tmp_path)
    approved(paths)
    info = write_trusted_prototype(BEHAVIOR, display_name="compose", tlon_paths=paths)
    (paths.capabilities_dir() / "shout/capability.tlon").write_text(PURE.replace('"!"', '"?"'))
    with pytest.raises(PrototypeError, match="shout"):
        instantiate(info.name, "worker", "Worker", model="mock/model", tlon_paths=paths)
    assert not paths.agent_config_dir("worker").exists()


def test_prototype_approval_revalidates_current_composite_dependencies(tmp_path: Path) -> None:
    paths = TlonPaths(home=tmp_path)
    approved(paths)
    info = write_staging_prototype(BEHAVIOR, display_name="compose", tlon_paths=paths)
    (paths.capabilities_dir() / "shout/capability.tlon").write_text(PURE.replace('"!"', '"?"'))
    with pytest.raises(PrototypeError, match="shout"):
        approve_prototype(info.name, paths)
    assert (paths.prototypes_staging_dir() / info.name / "behavior.tlon").exists()
    assert not (paths.prototypes_dir() / info.name).exists()
