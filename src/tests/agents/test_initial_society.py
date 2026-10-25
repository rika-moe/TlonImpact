"""Initial society lifecycle checks: all homes are temporary and entirely offline."""

import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from tlon.agents import initial_society
from tlon.agents.bootstrap import bootstrap_society
from tlon.base.config import AgentConfig, yaml_load_all
from tlon.base.paths import TlonPaths


def test_members_have_model_persona_and_local_contacts(tmp_path: Path) -> None:
    paths = TlonPaths(tmp_path)
    bootstrap_society(paths)
    agents = yaml_load_all(tmp_path)
    assert len(agents) == 30
    for agent_id, config in agents.items():
        assert config.llm.model_name == "deepseek/deepseek-flash"
        assert "max_tokens" not in config.llm.model_dump()
        assert "max_output_tokens" not in config.llm.model_dump()
        assert config.llm.top_p == 0.9
        assert config.docker.level == "L2"
        source = paths.agent_config_dir(agent_id) / "modules/work/main.tlon"
        assert source.is_file()
        assert "agent/respond" in source.read_text("utf-8")
        assert ":version 2" in paths.agent_behavior_file(agent_id).read_text("utf-8")
        for kind in ("IDENTITY", "SOUL", "RULES"):
            assert (paths.agent_config_dir(agent_id) / f"{kind}.md").read_text("utf-8")
        assert (
            paths.agent_contacts_file(agent_id)
            == paths.agent_workspace_dir(agent_id) / "CONTACTS.md"
        )
        assert not (paths.agent_config_dir(agent_id) / "CONTACTS.md").exists()
        contacts = paths.agent_contacts_file(agent_id).read_text("utf-8")
        addresses = [
            line.removeprefix("- 地址：")
            for line in contacts.splitlines()
            if line.startswith("- 地址：")
        ]
        assert 2 <= len(addresses) <= 4
        assert all(address.endswith("@tlon.internal") for address in addresses)
        assert all(
            address.removesuffix("@tlon.internal") in agents
            and address.removesuffix("@tlon.internal") != agent_id
            for address in addresses
        )


def test_restart_preserves_edits_and_deleted_members(tmp_path: Path) -> None:
    paths = TlonPaths(tmp_path)
    bootstrap_society(paths)
    member = next(iter(yaml_load_all(tmp_path)))
    paths.agent_soul_file(member).write_text("我的新想法", "utf-8")
    # Completed homes do not even consult the packaged resources.
    with patch.object(initial_society, "_new_plan", side_effect=AssertionError):
        bootstrap_society(paths)
        assert paths.agent_soul_file(member).read_text("utf-8") == "我的新想法"
        for member_id in yaml_load_all(tmp_path):
            shutil.rmtree(paths.agent_dir(member_id))
        bootstrap_society(paths)
        assert yaml_load_all(tmp_path) == {}


@pytest.mark.parametrize(
    "evidence", ["invalid-config", "program", "mail", "identity", "old-memory", "old-persona"]
)
def test_existing_society_is_skipped_permanently(tmp_path: Path, evidence: str) -> None:
    paths = TlonPaths(tmp_path)
    evidence_path = {
        "invalid-config": paths.agent_config_file("old-member"),
        "program": paths.agent_program_database_file("m-retired"),
        "mail": paths.mail_database_file(),
        "identity": paths.webui_identity_file(),
        "old-memory": paths.agent_memory_dir("old-member") / "claims.jsonl",
        "old-persona": paths.agent_identity_file("old-persona"),
    }[evidence]
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text("existing data", "utf-8")
    bootstrap_society(paths)
    assert json.loads(paths.initial_society_state_file().read_text())["status"] == "skipped"
    evidence_path.unlink()
    bootstrap_society(paths)
    assert not list(tmp_path.glob("m-*/config/agent.yaml"))


def test_partial_staging_resumes_with_same_addresses(tmp_path: Path) -> None:
    paths = TlonPaths(tmp_path)
    original = initial_society.instantiate
    count = 0

    def interrupted(*args: Any, **kwargs: Any) -> AgentConfig:
        nonlocal count
        count += 1
        if count == 4:
            raise OSError("interrupted staging")
        return original(*args, **kwargs)

    with patch.object(initial_society, "instantiate", interrupted), pytest.raises(OSError):
        bootstrap_society(paths)
    state = json.loads(paths.initial_society_state_file().read_text())
    assert not list(tmp_path.glob("m-*/config/agent.yaml"))
    bootstrap_society(paths)
    assert set(yaml_load_all(tmp_path)) == set(state["addresses"].values())


def test_partial_publication_preserves_published_persona(tmp_path: Path) -> None:
    paths = TlonPaths(tmp_path)
    publish = initial_society._publish_member
    count = 0

    def interrupted(target_paths: TlonPaths, key: str, address: str) -> None:
        nonlocal count
        count += 1
        if count == 4:
            raise OSError("interrupted publication")
        publish(target_paths, key, address)

    with patch.object(initial_society, "_publish_member", interrupted), pytest.raises(OSError):
        bootstrap_society(paths)
    published = next(iter(yaml_load_all(tmp_path)))
    paths.agent_identity_file(published).write_text("已发布之后的修改", "utf-8")
    with patch.object(initial_society, "_stage_members", side_effect=AssertionError):
        bootstrap_society(paths)
    assert len(yaml_load_all(tmp_path)) == 30
    assert paths.agent_identity_file(published).read_text("utf-8") == "已发布之后的修改"


@pytest.mark.parametrize("state", ["{broken", '{"status":"pending"}', '{"status":"unknown"}'])
def test_corrupt_state_fails_explicitly(tmp_path: Path, state: str) -> None:
    paths = TlonPaths(tmp_path)
    paths.initial_society_state_file().write_text(state)
    with pytest.raises(RuntimeError, match="状态损坏"):
        bootstrap_society(paths)
    assert not list(tmp_path.glob("m-*/config/agent.yaml"))


def test_separate_homes_have_separate_addresses(tmp_path: Path) -> None:
    a, b = TlonPaths(tmp_path / "a"), TlonPaths(tmp_path / "b")
    bootstrap_society(a)
    bootstrap_society(b)
    assert not set(yaml_load_all(a.tlon_home())) & set(yaml_load_all(b.tlon_home()))


def test_concurrent_initializers_use_one_plan(tmp_path: Path) -> None:
    paths = TlonPaths(tmp_path)
    # Library seeding is independently idempotent; isolate the society mutex here.
    bootstrap_society(TlonPaths(tmp_path / "source"))
    shutil.copytree(tmp_path / "source" / "prototypes", paths.prototypes_dir())
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(initial_society.seed_initial_society, [paths, paths]))
    assert sorted(results) == [0, 30]
    assert len(yaml_load_all(tmp_path)) == 30
