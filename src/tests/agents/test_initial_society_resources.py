"""Validate the shipped society as a coherent, instance-independent seed."""

import json
import re
from importlib.resources import files
from typing import Any

import pytest

_RESOURCE_ROOT = files("tlon.initial_society")
_MANIFEST: dict[str, Any] = json.loads(
    _RESOURCE_ROOT.joinpath("manifest.json").read_text(encoding="utf-8")
)
_ORDER = [
    ("confucius", "孔子", "wood"),
    ("leonardo-da-vinci", "达·芬奇", "fire"),
    ("michael-faraday", "法拉第", "earth"),
    ("margaret-hamilton", "玛格丽特·汉密尔顿", "metal"),
    ("li-qingzhao", "李清照", "water"),
]


def test_manifest_members_and_deployment_defaults() -> None:
    members = _MANIFEST["members"]
    assert {(m["key"], m["name"], m["element"]) for m in members} == set(_ORDER)
    assert len(members) == 5
    assert _MANIFEST["model"] == "deepseek/deepseek-flash"
    assert _MANIFEST["prototype"] == "p-dfb3a5ccdcf84d749c9d075182f12888"
    assert {
        entry.name
        for entry in _RESOURCE_ROOT.iterdir()
        if entry.is_dir() and entry.name != "__pycache__"
    } == {key for key, _, _ in _ORDER}
    for member in members:
        assert "organization" not in member
        assert re.search(r"[\u4e00-\u9fff]", member["background"])


def test_relations_are_the_approved_directed_five_element_cycles() -> None:
    relations = _MANIFEST["relations"]
    expected = {
        (key, _ORDER[(index + step) % 5][0], kind)
        for index, (key, _, _) in enumerate(_ORDER)
        for kind, step in (("love", 1), ("hate", 2))
    }
    assert len(relations) == 10
    assert {(r["source"], r["target"], r["kind"]) for r in relations} == expected
    assert all(re.search(r"[\u4e00-\u9fff]", r["reason"]) for r in relations)


@pytest.mark.parametrize("member", _MANIFEST["members"], ids=lambda member: member["key"])
def test_personas_are_chinese_clean_and_separated(member: dict[str, Any]) -> None:
    headings = {
        "IDENTITY.md": "# 自我定义",
        "SOUL.md": "# 价值观、性格与表达",
        "RULES.md": "# 工作规则与流程",
    }
    directory = _RESOURCE_ROOT.joinpath(member["key"])
    assert {entry.name for entry in directory.iterdir()} == set(headings)
    for filename, heading in headings.items():
        content = directory.joinpath(filename).read_text(encoding="utf-8")
        assert content.startswith(heading)
        assert len(re.findall(r"[\u4e00-\u9fff]", content)) >= 60
        assert not re.search(r"m-[0-9a-f]{32}", content)
        assert "/Users/" not in content
        assert "2026-10-18" not in content
        if filename != "IDENTITY.md":
            assert "初始合作约定" not in content


def test_persona_relationships_agree_with_outgoing_manifest_edges() -> None:
    names = {member["key"]: member["name"] for member in _MANIFEST["members"]}
    for relation in _MANIFEST["relations"]:
        soul = _RESOURCE_ROOT.joinpath(relation["source"], "SOUL.md").read_text("utf-8")
        kind = "爱／欣赏" if relation["kind"] == "love" else "恨／抵触"
        assert any(kind in line and names[relation["target"]] in line for line in soul.splitlines())
