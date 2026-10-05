"""proposeCapability — the experience entry of the capability channel
(issue #30, docs/design/capabilities.md).

A mutation is born as a tlosure artifact: the tool drafts the complete
capability artifact itself — one LLM call fed with the member's memory
digest + distilled lessons + the caller's intent and word name (no
behavior.tlon draft) — then through the ``write_staging_capability``
gate. Failures (garbage draft, name mismatch, validation, name conflict)
come back as ``success=False`` for the outer ReAct loop to retry — the
tool never retries internally. No near-kin check (same-name is a hard
structural rejection); the solvency gate is reused from proposeBehavior.
"""

from functools import partial
from pathlib import Path
from typing import Any

from tlon.agents.assembly import _render_trusted_capability_words
from tlon.agents.capabilities.spec import CAPABILITY_PURE_BUILTINS
from tlon.agents.capabilities.staging import approve_capability, write_staging_capability
from tlon.agents.session_journal import SessionJournal
from tlon.agents.vocabulary import MEMBER_ACTION_NAMES
from tlon.base.llm_client import LLMResponse
from tlon.base.paths import TlonPaths
from tlon.memory.manager import MemoryManager
from tlon.memory.models import MemoryConfig
from tlon.tools.agents.propose_capability import (
    _ACTION_WORDS,
    _ARTIFACT_HINT,
    _PURE_WORDS,
    ProposeCapabilityTool,
)

_DRAFT_ARTIFACT = """\
{:version 1
 :name "thread-digest"
 :description "把邮件主题列表蒸馏成一行摘要"
 :args [{:name "subjects" :type :list :doc "按时间升序的主题列表"}]
 :impl (fn [subjects] (join " / " subjects))
 :pure true
 :examples [{:args [["a" "b"]] :expect "a / b"}]}
"""

_INVALID_ARTIFACT = """\
{:version 1
 :name "wild-word"
 :description "组合未注册词汇"
 :args []
 :impl (fn [] (jump {:to ["x@tlon.internal"]}))
 :pure true}
"""

_OTHER_NAME_ARTIFACT = """\
{:version 1
 :name "other-word"
 :description "词名与请求不一致"
 :args []
 :impl (fn [] "x")
 :pure true}
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
    trusted_words: Any = None,
) -> ProposeCapabilityTool:
    if memory is None:
        memory = MemoryManager(agent_id, MemoryConfig(), data_dir=p.agent_dir(agent_id))
    return ProposeCapabilityTool(
        agent_id=agent_id,
        prototype=prototype,
        author_id=agent_id,
        llm_client=llm,  # type: ignore[arg-type]
        memory=memory,
        logs=SessionJournal(agent_id, p),
        writer=partial(write_staging_capability, tlon_paths=p),
        tlon_paths=p,
        solvency=solvency,
        trusted_words=trusted_words,
    )


class TestProposeCapability:
    async def test_main_path(self, tmp_path: Path) -> None:
        """经验 → 起草 → staging 提案：意图记 proposal_note、author 落元数据，
        起草 prompt 携带 digest、lessons、词名与意图（不读 behavior.tlon）。"""
        p = TlonPaths(home=tmp_path)
        agent_id = "c-base-00000001"
        lessons_file = p.memory_lessons_file(agent_id)
        lessons_file.parent.mkdir(parents=True, exist_ok=True)
        lessons_file.write_text("- 摘要别超过一行\n", encoding="utf-8")

        llm = _FakeLLM(f"```edn\n{_DRAFT_ARTIFACT}```\n")
        memory = MemoryManager(agent_id, MemoryConfig(), data_dir=p.agent_dir(agent_id))
        await memory.remember_fact("每周都要手整理线程摘要")
        tool = _make_tool(p, agent_id, llm, memory=memory)

        result = await tool.execute(intent="造一个词把线程主题蒸成一行", name="thread-digest")

        assert result.success, result.error
        assert result.data == {
            "name": "thread-digest",
            "trusted": False,
            "status": "pending_approval",
        }

        staging = p.capabilities_staging_dir() / "thread-digest"
        assert (staging / "capability.tlon").read_text(encoding="utf-8") == _DRAFT_ARTIFACT
        metadata = (staging / "capability.edn").read_text(encoding="utf-8")
        assert ":trusted false" in metadata
        assert f':author "{agent_id}"' in metadata
        assert ":proposal-note" in metadata and "把线程主题蒸成一行" in metadata

        # 起草调用：prompt 含 digest 事实 + lessons + 词名 + 意图，记账 purpose
        assert len(llm.calls) == 1
        call = llm.calls[0]
        assert call["purpose"] == "propose"
        assert "temperature" not in call
        assert call["timeout"] == 60.0
        prompt = call["messages"][0]["content"]
        assert "每周都要手整理线程摘要" in prompt
        assert "- 摘要别超过一行" in prompt
        assert "## 词名\nthread-digest" in prompt
        assert "造一个词把线程主题蒸成一行" in prompt

    async def test_trusted_words_in_prompt(self, tmp_path: Path) -> None:
        """受信词清单非空时拼进 user prompt（防重复造词 + 风格示范）。"""
        p = TlonPaths(home=tmp_path)
        agent_id = "c-base-00000001"
        llm = _FakeLLM(_DRAFT_ARTIFACT)
        tool = _make_tool(
            p, agent_id, llm, trusted_words=lambda: "- mail-triage（纯）：邮件分诊"
        )

        result = await tool.execute(intent="造词", name="thread-digest")

        assert result.success, result.error
        prompt = llm.calls[0]["messages"][0]["content"]
        assert "既有受信词" in prompt
        assert "- mail-triage（纯）：邮件分诊" in prompt

    async def test_empty_trusted_words_omitted(self, tmp_path: Path) -> None:
        """空库（空串）时 prompt 不带受信词 section。"""
        p = TlonPaths(home=tmp_path)
        agent_id = "c-base-00000001"
        llm = _FakeLLM(_DRAFT_ARTIFACT)
        tool = _make_tool(p, agent_id, llm, trusted_words=lambda: "")

        result = await tool.execute(intent="造词", name="thread-digest")

        assert result.success, result.error
        assert "既有受信词" not in llm.calls[0]["messages"][0]["content"]

    async def test_garbage_draft_rejected(self, tmp_path: Path) -> None:
        """起草 LLM 返回垃圾（无 EDN）→ success=False，staging 无残留。"""
        p = TlonPaths(home=tmp_path)
        agent_id = "c-base-00000001"
        tool = _make_tool(p, agent_id, _FakeLLM("我觉得可以造这样一个词……"))

        result = await tool.execute(intent="造词", name="junk")

        assert not result.success
        assert "EDN" in result.error
        assert not (p.capabilities_staging_dir() / "junk").exists()

    async def test_name_mismatch_fed_back(self, tmp_path: Path) -> None:
        """起草产物的 :name 与指定词名不一致 → success=False，staging 无残留。"""
        p = TlonPaths(home=tmp_path)
        agent_id = "c-base-00000001"
        tool = _make_tool(p, agent_id, _FakeLLM(_OTHER_NAME_ARTIFACT))

        result = await tool.execute(intent="造词", name="thread-digest")

        assert not result.success
        assert "thread-digest" in result.error
        assert not (p.capabilities_staging_dir() / "other-word").exists()

    async def test_invalid_artifact_fed_back(self, tmp_path: Path) -> None:
        """起草产出非法 artifact → CapabilityError 原文回喂 + hint，
        staging 无残留。"""
        p = TlonPaths(home=tmp_path)
        agent_id = "c-base-00000001"
        tool = _make_tool(p, agent_id, _FakeLLM(_INVALID_ARTIFACT))

        result = await tool.execute(intent="造词", name="wild-word")

        assert not result.success
        assert "jump" in result.error
        assert "hint" in result.data
        assert not (p.capabilities_staging_dir() / "wild-word").exists()

    async def test_name_conflict_fed_back(self, tmp_path: Path) -> None:
        """同名即拒：staging 已有同名词 → 错误原文回喂（改进走新名）。"""
        p = TlonPaths(home=tmp_path)
        agent_id = "c-base-00000001"
        write_staging_capability(_DRAFT_ARTIFACT, tlon_paths=p)
        tool = _make_tool(p, agent_id, _FakeLLM(_DRAFT_ARTIFACT))

        result = await tool.execute(intent="再造一次", name="thread-digest")

        assert not result.success
        assert "already exists" in result.error


class TestProposeCapabilitySolvency:
    """提案偿付闸：失去偿付能力的血脉不能提案；闸 fail-open。"""

    async def test_insolvent_bloodline_rejected_before_drafting(
        self, tmp_path: Path
    ) -> None:
        """闸排在起草调用之前：被闸时 LLM 一次都不调用，staging 无残留。"""
        p = TlonPaths(home=tmp_path)
        agent_id = "c-base-00000001"

        async def _insolvent(prototype: str) -> bool:
            return False

        llm = _FakeLLM(_DRAFT_ARTIFACT)
        tool = _make_tool(p, agent_id, llm, solvency=_insolvent)

        result = await tool.execute(intent="造词", name="thread-digest")

        assert not result.success
        assert "insolvent" in result.error
        assert llm.calls == []
        assert not (p.capabilities_staging_dir() / "thread-digest").exists()

    async def test_solvency_failure_is_fail_open(self, tmp_path: Path) -> None:
        """solvency 抛异常 → 放行（fail-open），提案照常走起草 + staging。"""
        p = TlonPaths(home=tmp_path)
        agent_id = "c-base-00000001"

        async def _boom(prototype: str) -> bool:
            raise OSError("fitness derivation broke")

        tool = _make_tool(p, agent_id, _FakeLLM(_DRAFT_ARTIFACT), solvency=_boom)

        result = await tool.execute(intent="造词", name="thread-digest")

        assert result.success, result.error


class TestTrustedWordsRenderer:
    """assembly 侧的受信词清单渲染（注入工具的零参 callable）。"""

    def test_render_trusted_capability_words(self, tmp_path: Path) -> None:
        p = TlonPaths(home=tmp_path)
        assert _render_trusted_capability_words(p) == ""

        write_staging_capability(_DRAFT_ARTIFACT, tlon_paths=p)
        approve_capability("thread-digest", p)

        rendered = _render_trusted_capability_words(p)
        assert "thread-digest" in rendered
        assert "纯" in rendered
        assert "把邮件主题列表蒸馏成一行摘要" in rendered


class TestHintVocabularyAnchor:
    """起草 hint 的词表与真实注册表防漂移锚定。"""

    def test_pure_words_match_load_gate_registry(self) -> None:
        assert set(_PURE_WORDS) == set(CAPABILITY_PURE_BUILTINS)
        assert " ".join(sorted(CAPABILITY_PURE_BUILTINS)) in _ARTIFACT_HINT

    def test_action_words_match_member_vocabulary(self) -> None:
        assert set(_ACTION_WORDS) == set(MEMBER_ACTION_NAMES)
        for word in MEMBER_ACTION_NAMES:
            assert word in _ARTIFACT_HINT
