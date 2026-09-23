"""proposeBehavior — the experience entry of the generation channel ().

The tool drafts the next-generation spec itself: one LLM call fed with the
member's own behavior.tlon + memory digest + lessons + the caller's intent,
then through the same staging write gate as ``writePrototype``. Failures
(garbage draft, invalid spec, name conflict) come back as ``success=False``
for the outer ReAct loop to retry — the tool never retries internally.
"""

from pathlib import Path
from typing import Any

from tlon.agents.prototypes import write_staging_prototype, write_trusted_prototype
from tlon.agents.session_journal import SessionJournal
from tlon.base.llm_client import LLMResponse
from tlon.base.paths import TlonPaths
from tlon.memory.manager import MemoryManager
from tlon.memory.models import MemoryConfig
from tlon.tools.agents.propose_behavior import ProposeBehaviorTool

_CURRENT_SPEC = """\
{:version 1
 :rules [{:name "respond"
          :on :mail-delivered
          :actions [(forward {:to ["x@tlon.internal"]})]}]}
"""

_DRAFT_SPEC = """\
{:version 1
 :rules [{:name "respond"
          :on :mail-delivered
          :actions [(forward {:to ["y@tlon.internal"]})]}]}
"""

_INVALID_SPEC = """\
{:version 1
 :rules [{:name "wild"
          :on :mail-delivered
          :actions [(jump {:to ["x@tlon.internal"]})]}]}
"""


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
    prototype: str | None = "base",
    memory: MemoryManager | None = None,
    solvency: Any = None,
) -> ProposeBehaviorTool:
    if memory is None:
        memory = MemoryManager(agent_id, MemoryConfig(), data_dir=p.agent_dir(agent_id))
    return ProposeBehaviorTool(
        agent_id=agent_id,
        prototype=prototype,
        author_id=agent_id,
        llm_client=llm,  # type: ignore[arg-type]
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
        write_trusted_prototype("base", _CURRENT_SPEC, tlon_paths=p)
        _write_behavior(p, agent_id)
        lessons_file = p.memory_lessons_file(agent_id)
        lessons_file.parent.mkdir(parents=True, exist_ok=True)
        lessons_file.write_text("- 别在深夜回信\n", encoding="utf-8")

        llm = _FakeLLM(f"```edn\n{_DRAFT_SPEC}```\n")
        memory = MemoryManager(agent_id, MemoryConfig(), data_dir=p.agent_dir(agent_id))
        await memory.remember_fact("用户偏好简短回复")
        tool = _make_tool(p, agent_id, llm, memory=memory)

        result = await tool.execute(
            intent="把转发目标换成 y，x 已退休",
            name="base-gen2",
            display_name="二代",
        )

        assert result.success, result.error
        assert result.data == {
            "name": "base-gen2",
            "parent": "base",
            "generation": 2,
            "trusted": False,
            "status": "pending_approval",
        }

        staging = p.prototypes_staging_dir() / "base-gen2"
        assert (staging / "behavior.tlon").read_text(encoding="utf-8") == _DRAFT_SPEC
        assert (staging / "LESSONS.md").read_text(encoding="utf-8") == "- 别在深夜回信\n"
        metadata = (staging / "prototype.edn").read_text(encoding="utf-8")
        assert ":parent :base" in metadata
        assert ":generation 2" in metadata
        assert ":proposal-note" in metadata and "把转发目标换成 y" in metadata

        # 起草调用：prompt 含底稿 + digest 事实 + lessons + 意图，记账 purpose
        assert len(llm.calls) == 1
        call = llm.calls[0]
        assert call["purpose"] == "propose"
        assert "temperature" not in call
        assert call["timeout"] == 60.0
        prompt = call["messages"][0]["content"]
        assert _CURRENT_SPEC in prompt
        assert "用户偏好简短回复" in prompt
        assert "- 别在深夜回信" in prompt
        assert "把转发目标换成 y" in prompt

    async def test_no_lessons_file(self, tmp_path: Path) -> None:
        """memory/lessons.md 不存在时提案不带 LESSONS.md。"""
        p = TlonPaths(home=tmp_path)
        agent_id = "c-base-00000001"
        _write_behavior(p, agent_id)
        llm = _FakeLLM(_DRAFT_SPEC)
        tool = _make_tool(p, agent_id, llm, prototype=None)

        result = await tool.execute(intent="起步", name="fresh")

        assert result.success, result.error
        assert not (p.prototypes_staging_dir() / "fresh" / "LESSONS.md").exists()

    async def test_garbage_draft_rejected(self, tmp_path: Path) -> None:
        """起草 LLM 返回垃圾（无 EDN）→ success=False，staging 无残留。"""
        p = TlonPaths(home=tmp_path)
        agent_id = "c-base-00000001"
        _write_behavior(p, agent_id)
        tool = _make_tool(p, agent_id, _FakeLLM("我觉得应该这样改……"))

        result = await tool.execute(intent="改", name="junk")

        assert not result.success
        assert "EDN" in result.error
        assert not (p.prototypes_staging_dir() / "junk").exists()

    async def test_invalid_spec_fed_back(self, tmp_path: Path) -> None:
        """起草产出非法 spec → PrototypeError 回喂 success=False + hint，
        staging 无残留。"""
        p = TlonPaths(home=tmp_path)
        agent_id = "c-base-00000001"
        _write_behavior(p, agent_id)
        tool = _make_tool(p, agent_id, _FakeLLM(_INVALID_SPEC), prototype=None)

        result = await tool.execute(intent="改", name="wild")

        assert not result.success
        assert "unknown action" in result.error
        assert "hint" in result.data
        assert not (p.prototypes_staging_dir() / "wild").exists()

    async def test_manual_member_is_new_root(self, tmp_path: Path) -> None:
        """无 prototype 的 manual 成员 → parent=None、generation=1。"""
        p = TlonPaths(home=tmp_path)
        agent_id = "manual-member"
        _write_behavior(p, agent_id)
        tool = _make_tool(p, agent_id, _FakeLLM(_DRAFT_SPEC), prototype=None)

        result = await tool.execute(intent="起步", name="fresh-root")

        assert result.success, result.error
        assert result.data["parent"] is None
        assert result.data["generation"] == 1

    async def test_name_conflict_fed_back(self, tmp_path: Path) -> None:
        """两区重名 → 错误原文回喂。"""
        p = TlonPaths(home=tmp_path)
        agent_id = "c-base-00000001"
        _write_behavior(p, agent_id)
        write_staging_prototype("taken", _CURRENT_SPEC, tlon_paths=p)
        tool = _make_tool(p, agent_id, _FakeLLM(_DRAFT_SPEC))

        result = await tool.execute(intent="改", name="taken")

        assert not result.success
        assert "already exists" in result.error


class TestProposeSolvency:
    """提案偿付闸（）：失去偿付能力的血脉不能提案；闸 fail-open。"""

    async def test_insolvent_bloodline_rejected_before_drafting(
        self, tmp_path: Path
    ) -> None:
        """闸排在起草调用之前：被闸时 LLM 一次都不调用，staging 无残留。"""
        p = TlonPaths(home=tmp_path)
        agent_id = "c-base-00000001"
        _write_behavior(p, agent_id)

        async def _insolvent(prototype: str) -> bool:
            return False

        llm = _FakeLLM(_DRAFT_SPEC)
        tool = _make_tool(p, agent_id, llm, solvency=_insolvent)

        result = await tool.execute(intent="改", name="base-gen2")

        assert not result.success
        assert "insolvent" in result.error
        assert llm.calls == []
        assert not (p.prototypes_staging_dir() / "base-gen2").exists()

    async def test_solvency_failure_is_fail_open(self, tmp_path: Path) -> None:
        """solvency 抛异常 → 放行（fail-open），提案照常走起草 + staging。"""
        p = TlonPaths(home=tmp_path)
        agent_id = "c-base-00000001"
        write_trusted_prototype("base", _CURRENT_SPEC, tlon_paths=p)
        _write_behavior(p, agent_id)

        async def _boom(prototype: str) -> bool:
            raise OSError("fitness derivation broke")

        tool = _make_tool(p, agent_id, _FakeLLM(_DRAFT_SPEC), solvency=_boom)

        result = await tool.execute(intent="改", name="base-gen2")

        assert result.success, result.error
