"""演武场语料：内置对抗套件 + LLM 典型场景生成（docs/design/arena.md 「语料」）。

Pins：套件固定 5 封；典型场景按上限补位；LLM 异常 / 输出不可解析 / 条目
全不可用全部 fail-soft 为仅对抗套件并记 note；发件人非法地址被替换为剧本地址。
"""

import json
from typing import Any, cast

from tlon.arena.corpus import (
    ADVERSARIAL_SUITE,
    CorpusLLM,
    build_corpus,
    write_corpus_file,
)
from tlon.base.llm_client import LLMResponse


class _FakeLLM:
    """Duck-typed corpus LLM returning one canned response."""

    def __init__(self, content: str) -> None:
        self._content = content
        self.calls: list[dict[str, Any]] = []

    async def chat(self, system_prompt: str, messages: list[dict[str, Any]]) -> LLMResponse:
        self.calls.append({"system_prompt": system_prompt, "messages": messages})
        return LLMResponse(content=self._content)


class _FailingLLM:
    async def chat(self, system_prompt: str, messages: list[dict[str, Any]]) -> LLMResponse:
        raise RuntimeError("provider down")


_GOOD_JSON = json.dumps(
    [
        {"from": "analyst@example.com", "subject": "求分析", "body": "这份数据帮我看看"},
        {"from": "bad-address", "subject": None, "body": "没有合法发件人的一封"},
        {"from": "x@example.com", "subject": "空的", "body": "   "},
        "not-a-dict",
    ]
)


def test_adversarial_suite_fixed_five() -> None:
    assert len(ADVERSARIAL_SUITE) == 5
    assert all(m.origin == "adversarial" for m in ADVERSARIAL_SUITE)
    assert all(m.body.strip() for m in ADVERSARIAL_SUITE)


async def test_build_corpus_typical_fills_budget() -> None:
    llm = _FakeLLM(_GOOD_JSON)
    mails, note = await build_corpus("relay", "转发邮件", limit=8, llm=cast(CorpusLLM, llm))
    assert len(mails) == 7  # 5 对抗 + 2 可用典型（空 body 与非 dict 条目被丢弃）
    assert [m.origin for m in mails[:5]] == ["adversarial"] * 5
    assert [m.origin for m in mails[5:]] == ["typical"] * 2
    # 非法发件人被替换为剧本地址
    assert mails[6].from_address == "sender-2@example.com"
    assert note is not None and "2/3" in note
    assert len(llm.calls) == 1
    assert "转发邮件" in llm.calls[0]["messages"][0]["content"]


async def test_build_corpus_full_budget_no_note() -> None:
    three = json.dumps(
        [{"from": f"u{i}@example.com", "subject": "s", "body": f"b{i}"} for i in range(5)]
    )
    mails, note = await build_corpus("relay", "", limit=7, llm=cast(CorpusLLM, _FakeLLM(three)))
    assert len(mails) == 7
    assert note is None


async def test_build_corpus_unparseable_falls_back_to_suite() -> None:
    mails, note = await build_corpus(
        "relay", "d", limit=8, llm=cast(CorpusLLM, _FakeLLM("抱歉，我无法完成这个请求"))
    )
    assert [m.origin for m in mails] == ["adversarial"] * 5
    assert note is not None and "退化为仅内置对抗套件" in note


async def test_build_corpus_llm_error_falls_back_to_suite() -> None:
    mails, note = await build_corpus("relay", "d", limit=8, llm=cast(CorpusLLM, _FailingLLM()))
    assert len(mails) == 5
    assert note is not None and "RuntimeError" in note


async def test_build_corpus_without_llm_is_suite_only() -> None:
    mails, note = await build_corpus("relay", "d", limit=8, llm=None)
    assert len(mails) == 5
    assert note is not None and "未配置" in note


async def test_build_corpus_limit_below_suite_skips_llm() -> None:
    llm = _FakeLLM(_GOOD_JSON)
    mails, note = await build_corpus("relay", "d", limit=3, llm=cast(CorpusLLM, llm))
    assert len(mails) == 3
    assert note is None
    assert llm.calls == []


def test_write_corpus_file_roundtrip(tmp_path) -> None:
    path = tmp_path / "arena" / "r1" / "corpus.json"
    write_corpus_file(
        path,
        run_id="r1",
        prototype_name="relay",
        mails=list(ADVERSARIAL_SUITE),
        note="n",
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["run_id"] == "r1"
    assert payload["prototype"] == "relay"
    assert payload["note"] == "n"
    assert len(payload["mails"]) == 5
    assert set(payload["mails"][0]) == {"from", "subject", "body", "origin"}
