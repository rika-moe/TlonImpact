"""behavior-propose — the experience entry of the generation channel ().

The tool drafts the next-generation spec itself: one LLM call fed with the
member's own behavior.tlon + memory digest + lessons + the caller's intent,
then through the same staging write gate as ``prototype-write``. Failures
(garbage draft, invalid spec, name conflict) come back as ``success=False``
for the outer ReAct loop to retry — the tool never retries internally.
"""

import json
from pathlib import Path
from typing import Any

from tests.evolution_helpers import make_behavior_tool
from tlon.agents.prototypes.management import write_trusted_prototype
from tlon.agents.prototypes.staging import write_staging_prototype
from tlon.agents.session_journal import SessionJournal
from tlon.base.llm_client import LLMResponse
from tlon.base.paths import TlonPaths
from tlon.memory.manager import MemoryManager
from tlon.memory.models import MemoryConfig
from tlon.operations.adapters import OperationTool

_CURRENT_SPEC = '{:version 1\n :rules [{:name "respond"\n          :on :mail-delivered\n          :actions [(mail-forward {:mail_id (mail-field msg :id) :to ["x@tlon.internal"]})]}]}\n'

BUNDLE_BEHAVIOR = "{:version 2 :handlers {:mail-delivered work.reply/handle} :fuel 1000}"
BUNDLE_MODULES = {
    "work/reply.tlon": "(ns work.reply (:require [work.helper :as h]) (:export handle)) (defn handle [event] (h/result))",
    "work/helper.tlon": '(ns work.helper (:export result)) (defn result [] {:result "ok"})',
}

_DRAFT_SPEC = BUNDLE_BEHAVIOR
_DRAFT_JSON = json.dumps({"behavior": BUNDLE_BEHAVIOR, "modules": BUNDLE_MODULES})
_INVALID_SPEC = json.dumps(
    {
        "behavior": BUNDLE_BEHAVIOR,
        "modules": {
            **BUNDLE_MODULES,
            "work/helper.tlon": "(ns work.helper (:export result)) (defn result [] (jump {}))",
        },
    }
)


class _FakeLLM:
    """Duck-typed LLMClient returning one canned response."""

    def __init__(self, content: str) -> None:
        self._content = content
        self.calls: list[dict[str, Any]] = []

    async def chat(self, **kwargs: Any) -> LLMResponse:
        self.calls.append(kwargs)
        return LLMResponse(content=self._content)


def _make_tool(
    p: TlonPaths,
    agent_id: str,
    llm: _FakeLLM,
    prototype: str | None = None,
    memory: MemoryManager | None = None,
    solvency: Any = None,
) -> OperationTool:
    if memory is None:
        memory = MemoryManager(agent_id, MemoryConfig(), data_dir=p.agent_dir(agent_id))
    return make_behavior_tool(
        agent_id=agent_id,
        prototype=prototype,
        author_id=agent_id,
        llm_client=llm,
        memory=memory,
        logs=SessionJournal(agent_id, p),
        writer=lambda *a, **kw: write_staging_prototype(*a, tlon_paths=p, **kw),
        tlon_paths=p,
        solvency=solvency,
    )


def _write_behavior(p: TlonPaths, agent_id: str, spec: str = _CURRENT_SPEC) -> None:
    behavior_file = p.agent_behavior_file(agent_id)
    behavior_file.parent.mkdir(parents=True, exist_ok=True)
    behavior_file.write_text(spec, encoding="utf-8")


class TestProposeBehavior:
    async def test_main_path(self, tmp_path: Path) -> None:
        """经验 → 起草 → staging 提案：谱系/意图/教训随包，起草 prompt
        携带底稿与 digest。"""
        p = TlonPaths(home=tmp_path)
        agent_id = "c-base-00000001"
        created_1 = write_trusted_prototype(_CURRENT_SPEC, tlon_paths=p, display_name="定址转发")
        _write_behavior(p, agent_id)
        lessons_file = p.memory_lessons_file(agent_id)
        lessons_file.parent.mkdir(parents=True, exist_ok=True)
        lessons_file.write_text("- 别在深夜回信\n", encoding="utf-8")
        llm = _FakeLLM(_DRAFT_JSON)
        memory = MemoryManager(agent_id, MemoryConfig(), data_dir=p.agent_dir(agent_id))
        await memory.remember_fact("用户偏好简短回复")
        tool = _make_tool(p, agent_id, llm, memory=memory, prototype=created_1.name)
        result = await tool.execute_context(
            {"intent": "把转发目标换成 y，x 已退休", "display_name": "二代"}
        )
        assert result.success, result.error
        assert result.data == {
            "name": result.data["name"],
            "parent": created_1.name,
            "generation": 2,
            "trusted": False,
            "status": "pending_approval",
        }
        staging = p.prototypes_staging_dir() / result.data["name"]
        assert (staging / "behavior.tlon").read_text(encoding="utf-8") == _DRAFT_SPEC + "\n"
        assert (staging / "LESSONS.md").read_text(encoding="utf-8") == "- 别在深夜回信\n"
        metadata = (staging / "prototype.tlon").read_text(encoding="utf-8")
        assert f":parent :{created_1.name}" in metadata
        assert ":generation 2" in metadata
        assert ":proposal-note" in metadata and "把转发目标换成 y" in metadata
        assert len(llm.calls) == 1
        call = llm.calls[0]
        assert call["purpose"] == "propose"
        assert "temperature" not in call
        assert call["timeout"] == 60.0
        prompt = call["messages"][0]["content"]
        assert (
            _CURRENT_SPEC
            in json.loads(prompt.split("## 当前完整程序包\n")[1].split("\n\n")[0])["behavior"]
        )
        assert "用户偏好简短回复" in prompt
        assert "- 别在深夜回信" in prompt
        assert "把转发目标换成 y" in prompt

    async def test_no_lessons_file(self, tmp_path: Path) -> None:
        """memory/lessons.md 不存在时提案不带 LESSONS.md。"""
        p = TlonPaths(home=tmp_path)
        agent_id = "c-base-00000001"
        _write_behavior(p, agent_id)
        llm = _FakeLLM(_DRAFT_JSON)
        tool = _make_tool(p, agent_id, llm, prototype=None)
        result = await tool.execute_context({"intent": "起步", "display_name": "定址转发"})
        assert result.success, result.error
        assert not (p.prototypes_staging_dir() / result.data["name"] / "LESSONS.md").exists()

    async def test_garbage_draft_rejected(self, tmp_path: Path) -> None:
        """起草 LLM 返回垃圾（无 EDN）→ success=False，staging 无残留。"""
        p = TlonPaths(home=tmp_path)
        agent_id = "c-base-00000001"
        _write_behavior(p, agent_id)
        tool = _make_tool(p, agent_id, _FakeLLM("我觉得应该这样改……"))
        result = await tool.execute_context({"intent": "改", "display_name": "定址转发"})
        assert not result.success
        assert "JSON" in result.error
        assert not p.prototypes_staging_dir().exists()

    async def test_invalid_spec_fed_back(self, tmp_path: Path) -> None:
        """起草产出非法 spec → PrototypeError 回喂 success=False + hint，
        staging 无残留。"""
        p = TlonPaths(home=tmp_path)
        agent_id = "c-base-00000001"
        _write_behavior(p, agent_id)
        tool = _make_tool(p, agent_id, _FakeLLM(_INVALID_SPEC), prototype=None)
        result = await tool.execute_context({"intent": "改", "display_name": "定址转发"})
        assert not result.success
        assert "jump" in result.error
        assert "hint" in result.data
        assert not p.prototypes_staging_dir().exists()

    async def test_manual_member_is_new_root(self, tmp_path: Path) -> None:
        """无 prototype 的 manual 成员 → parent=None、generation=1。"""
        p = TlonPaths(home=tmp_path)
        agent_id = "manual-member"
        _write_behavior(p, agent_id)
        tool = _make_tool(p, agent_id, _FakeLLM(_DRAFT_JSON), prototype=None)
        result = await tool.execute_context({"intent": "起步", "display_name": "定址转发"})
        assert result.success, result.error
        assert result.data["parent"] is None
        assert result.data["generation"] == 1

    async def test_name_conflict_fed_back(self, tmp_path: Path, monkeypatch) -> None:
        """两区重名 → 错误原文回喂。"""
        p = TlonPaths(home=tmp_path)
        agent_id = "c-base-00000001"
        _write_behavior(p, agent_id)
        created_1 = write_staging_prototype(_CURRENT_SPEC, tlon_paths=p, display_name="定址转发")
        monkeypatch.setattr(
            "tlon.agents.prototypes.staging.new_prototype_id", lambda: created_1.name
        )
        tool = _make_tool(p, agent_id, _FakeLLM(_DRAFT_JSON))
        result = await tool.execute_context({"intent": "改", "display_name": "定址转发"})
        assert not result.success
        assert "already exists" in result.error


class TestProposeSolvency:
    """提案偿付闸：失去偿付能力的血脉不能提案；闸 fail-open。"""

    async def test_insolvent_bloodline_rejected_before_drafting(self, tmp_path: Path) -> None:
        """闸排在起草调用之前：被闸时 LLM 一次都不调用，staging 无残留。"""
        p = TlonPaths(home=tmp_path)
        agent_id = "c-base-00000001"
        _write_behavior(p, agent_id)

        async def _insolvent(prototype: str) -> bool:
            return False

        llm = _FakeLLM(_DRAFT_JSON)
        tool = _make_tool(p, agent_id, llm, solvency=_insolvent, prototype="broke")
        result = await tool.execute_context({"intent": "改", "display_name": "定址转发"})
        assert not result.success
        assert "insolvent" in result.error
        assert llm.calls == []
        assert not p.prototypes_staging_dir().exists()

    async def test_solvency_failure_is_fail_open(self, tmp_path: Path) -> None:
        """solvency 抛异常 → 放行（fail-open），提案照常走起草 + staging。"""
        p = TlonPaths(home=tmp_path)
        agent_id = "c-base-00000001"
        created_1 = write_trusted_prototype(_CURRENT_SPEC, tlon_paths=p, display_name="定址转发")
        _write_behavior(p, agent_id)

        async def _boom(prototype: str) -> bool:
            raise OSError("fitness derivation broke")

        tool = _make_tool(
            p, agent_id, _FakeLLM(_DRAFT_JSON), solvency=_boom, prototype=created_1.name
        )
        result = await tool.execute_context({"intent": "改", "display_name": "定址转发"})
        assert result.success, result.error


async def test_propose_rejects_old_name_or_missing_label_before_drafting(tmp_path: Path) -> None:
    p = TlonPaths(home=tmp_path)
    llm = _FakeLLM(_DRAFT_JSON)
    tool = _make_tool(p, "member", llm)
    assert "name" not in tool.parameters["properties"]
    assert "display_name" in tool.parameters["required"]
    for args in (
        {"intent": "改", "name": "developer", "display_name": "定址转发"},
        {"intent": "改"},
        {"intent": "改", "display_name": "  "},
    ):
        result = await tool.execute_context(args)
        assert not result.success
        assert "name" in result.error
    assert llm.calls == []
    assert not p.prototypes_staging_dir().exists()


async def test_proposal_reads_current_modules_and_keeps_complete_copy(tmp_path: Path) -> None:
    from tlon.agents.prototypes.staging import approve_prototype

    p = TlonPaths(home=tmp_path)
    _write_behavior(p, "author", BUNDLE_BEHAVIOR)
    module_root = p.agent_behavior_file("author").parent / "modules"
    for path, source in BUNDLE_MODULES.items():
        target = module_root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source)
    llm = _FakeLLM(_DRAFT_JSON)
    result = await _make_tool(p, "author", llm).execute_context(
        {"intent": "保留模块", "display_name": "完整程序"}
    )
    assert result.success, result.error
    supplied = json.loads(
        llm.calls[0]["messages"][0]["content"].split("## 当前完整程序包\n")[1].split("\n\n")[0]
    )
    assert supplied["modules"] == BUNDLE_MODULES
    name = result.data["name"]
    approve_prototype(name, p)
    # The approved author sources do not point back to the live member.
    (module_root / "work/helper.tlon").write_text("changed")
    assert (p.prototypes_dir() / name / "modules/work/helper.tlon").read_text() == BUNDLE_MODULES[
        "work/helper.tlon"
    ]


async def test_draft_requires_complete_strict_v2_bundle(tmp_path: Path) -> None:
    p = TlonPaths(home=tmp_path)
    _write_behavior(p, "author")
    drafts = [
        "```json\n" + _DRAFT_JSON + "\n```",
        json.dumps({"behavior": _CURRENT_SPEC, "modules": BUNDLE_MODULES}),
        json.dumps({"behavior": BUNDLE_BEHAVIOR, "modules": {}}),
        json.dumps(
            {
                "behavior": BUNDLE_BEHAVIOR,
                "modules": {"work/reply.tlon": BUNDLE_MODULES["work/reply.tlon"]},
            }
        ),
        json.dumps(
            {
                "behavior": BUNDLE_BEHAVIOR,
                "modules": {
                    **BUNDLE_MODULES,
                    "../escape.tlon": "(ns escape (:export x)) (defn x [] 1)",
                },
            }
        ),
        json.dumps(
            {
                "behavior": BUNDLE_BEHAVIOR,
                "modules": {
                    "work/reply.tlon": BUNDLE_MODULES["work/reply.tlon"],
                    "other.tlon": "(ns other (:export x)) (defn x [] 1)",
                },
            }
        ),
    ]
    for draft in drafts:
        llm = _FakeLLM(draft)
        result = await _make_tool(p, "author", llm).execute_context(
            {"intent": "改进", "display_name": "待审"}
        )
        assert not result.success, draft
        assert len(llm.calls) == 1
        assert not p.prototypes_staging_dir().exists()
