"""Packaged module programs boot, retain local routing and copy complete sources."""

from pathlib import Path

import pytest

from tests.prototype_helpers import builtin_id
from tlon.agents.program_package import read_module_sources, validate_program_sources
from tlon.agents.prototypes.catalog import seed_prototypes
from tlon.agents.prototypes.instantiate import instantiate
from tlon.base.paths import TlonPaths

LABELS = (
    "计划后执行",
    "短回合交流",
    "标准工具迭代",
    "标准文件迭代",
    "定址转发",
    "标准无执行迭代",
    "证据核验",
    "条件路由",
)


@pytest.mark.parametrize("label", LABELS)
def test_complete_builtin_module_package(tmp_path: Path, label: str) -> None:
    paths = TlonPaths(tmp_path)
    seed_prototypes(paths)
    params = (
        {"sender": "sender@tlon.internal", "pattern": "URGENT", "to": ["target@tlon.internal"]}
        if label == "条件路由"
        else {"to": ["target@tlon.internal"]}
        if label == "定址转发"
        else None
    )
    config = instantiate(
        builtin_id(label), "new-member", "new", model="test/model", params=params, tlon_paths=paths
    )
    behavior = paths.agent_behavior_file(config.id).read_text()
    modules = read_module_sources(paths.agent_config_dir(config.id) / "modules")
    spec = validate_program_sources(behavior, modules)
    assert spec.version == 2
    assert {handler.event_name: handler.entry for handler in spec.handlers} == {
        "boot": "work.main/boot",
        "mail-delivered": "work.main/handle",
    }
    assert spec.execution_level == (
        "L2"
        if label in {"计划后执行", "标准工具迭代", "证据核验"}
        else "L1"
        if label == "标准文件迭代"
        else "L0"
    )
    assert config.docker.level == spec.execution_level
    assert len(modules) == 1
    assert "run-loop" not in behavior + str(modules)
    assert "${" not in str(modules)
    if params:
        assert "target@tlon.internal" in modules["work/main.tlon"]


def test_seed_preserves_existing_package_sources(tmp_path: Path) -> None:
    paths = TlonPaths(tmp_path)
    assert seed_prototypes(paths) == 24
    source = paths.prototypes_dir() / builtin_id("标准工具迭代") / "modules/work/main.tlon"
    source.write_text("user-owned source")
    assert seed_prototypes(paths) == 0
    assert source.read_text() == "user-owned source"


@pytest.mark.parametrize(
    "label,sender,content,recipient,forwarded",
    [
        ("定址转发", "sender@tlon.internal", "hello", "member", True),
        ("条件路由", "sender@tlon.internal", "URGENT: help", "member", True),
        ("条件路由", "other@tlon.internal", "URGENT: help", "member", False),
        ("条件路由", "sender@tlon.internal", "ordinary", "member", False),
        ("条件路由", "sender@tlon.internal", "URGENT: help", "other", False),
    ],
)
def test_local_routing_never_calls_model(
    tmp_path: Path, label: str, sender: str, content: str, recipient: str, forwarded: bool
) -> None:
    from tlon.agents.program_package import program_builtins, program_module_sources
    from tlosure.builtins import Builtin
    from tlosure.edn import loads
    from tlosure.eval import eval_form
    from tlosure.modules import ModulePackage

    paths = TlonPaths(tmp_path)
    seed_prototypes(paths)
    instantiate(
        builtin_id(label),
        "member",
        "member",
        model="test/model",
        tlon_paths=paths,
        params={
            "sender": "sender@tlon.internal",
            "pattern": "URGENT",
            "to": ["target@tlon.internal"],
        }
        if label == "条件路由"
        else {"to": ["target@tlon.internal"]},
    )
    calls: list[dict] = []
    builtins = program_builtins()
    builtins["mail-forward"] = Builtin(lambda env, args: calls.append(args), pure=False)
    builtins["mail-mark-read"] = Builtin(lambda env, args: None, pure=False)
    modules = read_module_sources(paths.agent_config_dir("member") / "modules")
    package = ModulePackage.from_sources(program_module_sources(modules), builtins)
    event = {
        "sender": sender,
        "mail": {"id": "mail", "content": content},
        "payload": {"recipient": recipient},
        "self": {"id": "member"},
    }
    eval_form(
        loads("(handle event)"),
        {"handle": package.entry("work.main/handle"), "event": event},
        builtins,
        fuel=1000,
    )
    assert len(calls) == int(forwarded)
    if forwarded:
        assert calls == [{"mail_id": "mail", "to": ["target@tlon.internal"]}]


def test_seed_does_not_mix_new_modules_into_existing_package(tmp_path: Path) -> None:
    paths = TlonPaths(tmp_path)
    existing = paths.prototypes_dir() / builtin_id("标准工具迭代")
    existing.mkdir(parents=True)
    (existing / "behavior.tlon").write_text("user-owned behavior")
    seed_prototypes(paths)
    assert (existing / "behavior.tlon").read_text() == "user-owned behavior"
    assert not (existing / "modules").exists()
    assert not (existing / "prototype.tlon").exists()
