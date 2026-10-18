"""Validate the shipped society as a coherent, instance-independent seed."""

import json
import re
from collections import Counter
from importlib.resources import files
from typing import Any

import pytest

_RESOURCE_ROOT = files("tlon.initial_society")
_MANIFEST: dict[str, Any] = json.loads(
    _RESOURCE_ROOT.joinpath("manifest.json").read_text(encoding="utf-8")
)
_EXPECTED_NAMES = {
    "王阳明",
    "庄子",
    "玄奘",
    "苏格拉底",
    "图灵",
    "博尔赫斯",
    "凯恩斯",
    "亚当·斯密",
    "冯·诺依曼",
    "乔布斯",
    "鲁迅",
    "奥斯特罗姆",
    "西蒙",
    "屠呦呦",
    "孔子",
    "墨子",
    "韩非",
    "老子",
    "孙武",
    "哈耶克",
    "格蕾丝·霍珀",
    "戴明",
    "威廉·莫里斯",
    "伊本·赫勒敦",
    "简·雅各布斯",
    "司马迁",
    "蕾切尔·卡森",
    "艾达·B·威尔斯",
    "蒙台梭利",
    "厄休拉·勒古恩",
}
_EXPECTED_GROUPS = {
    "问学社": {"孔子", "苏格拉底", "王阳明", "蒙台梭利"},
    "兼济工坊": {"墨子", "格蕾丝·霍珀", "戴明", "威廉·莫里斯"},
    "自由秩序研究会": {"亚当·斯密", "哈耶克", "简·雅各布斯"},
    "歧路文学社": {"庄子", "博尔赫斯", "厄休拉·勒古恩"},
    "公共事务研讨社": {"凯恩斯", "奥斯特罗姆", "西蒙"},
}


def test_manifest_members_and_deployment_defaults() -> None:
    members = _MANIFEST["members"]
    assert len(members) == 30
    assert {member["name"] for member in members} == _EXPECTED_NAMES
    assert len({member["key"] for member in members}) == 30
    assert _MANIFEST["model"] == "deepseek/deepseek-flash"
    assert _MANIFEST["prototype"] == "p-dfb3a5ccdcf84d749c9d075182f12888"
    for member in members:
        assert re.fullmatch(r"[a-z]+(?:-[a-z]+)*", member["key"])
        assert re.search(r"[\u4e00-\u9fff]", member["background"])


def test_relations_are_sparse_connected_and_bidirectionally_meaningful() -> None:
    keys = {member["key"] for member in _MANIFEST["members"]}
    relations = _MANIFEST["relations"]
    edges = {frozenset(edge) for edge in relations}
    assert len(relations) == len(edges) == 45
    degree: Counter[str] = Counter()
    adjacency: dict[str, set[str]] = {key: set() for key in keys}
    for left, right in relations:
        assert left in keys and right in keys and left != right
        degree.update((left, right))
        adjacency[left].add(right)
        adjacency[right].add(left)
    assert set(degree) == keys
    assert all(2 <= count <= 4 for count in degree.values())
    visited: set[str] = set()
    pending = [next(iter(keys))]
    while pending:
        current = pending.pop()
        if current not in visited:
            visited.add(current)
            pending.extend(adjacency[current] - visited)
    assert visited == keys
    for required in (
        ("morris", "le-guin"),
        ("jacobs", "ostrom"),
        ("han-fei", "simon"),
        ("xuanzang", "tu-youyou"),
    ):
        assert frozenset(required) in edges


def test_initial_groups_are_small_voluntary_and_internally_connected() -> None:
    members = _MANIFEST["members"]
    groups: dict[str, set[str]] = {}
    edges = {frozenset(edge) for edge in _MANIFEST["relations"]}
    for member in members:
        if organization := member.get("organization"):
            groups.setdefault(organization, set()).add(member["name"])
            identity = _RESOURCE_ROOT.joinpath(member["key"], "IDENTITY.md").read_text(
                encoding="utf-8"
            )
            assert organization in identity
            assert "退出" in identity
            assert "没有预设上下级或固定职务" in identity
            assert "不表示已经完成任何共同项目" in identity
    assert groups == _EXPECTED_GROUPS
    assert sum("organization" in member for member in members) == 17
    assert sum("organization" not in member for member in members) == 13
    for organization in groups:
        group_keys = {
            member["key"] for member in members if member.get("organization") == organization
        }
        for left in group_keys:
            for right in group_keys - {left}:
                assert frozenset((left, right)) in edges


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
            assert not any(group in content for group in _EXPECTED_GROUPS)
